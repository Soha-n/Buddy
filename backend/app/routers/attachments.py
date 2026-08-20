"""File upload, indexing status and the sandboxed chart runner.

Upload responds as soon as the files are stored, then indexes them in a
background task. A 40-page PDF takes tens of seconds to embed, and holding the
request open for that would be indistinguishable from a stalled upload; instead
each attachment carries a status the UI polls until it reads 'ready'.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.models import attachment_store as store
from app.models import conversation_store as conversations
from app.models.schemas import (
    AttachmentsListResponse,
    ExcludedModel,
    RunCodeRequest,
    RunCodeResponse,
    UploadResponse,
)
from app.paths import data_root
from app.services import code_runner, extraction, rag

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

# Per-file ceiling. Generous for documents and spreadsheets while keeping a
# stray multi-gigabyte file from being read into memory.
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 10


def _uploads_dir() -> Path:
    """Where retained upload bytes live, alongside the SQLite file.

    Images (re-sent to the model on later turns) and spreadsheets (opened by the
    chart sandbox) are kept. Prose documents are discarded once their text has
    been chunked and embedded.
    """
    # Beside the database, in the writable data directory. Deriving this from
    # __file__ would put uploads inside the install directory - Program Files,
    # which a normal user cannot write to and an update replaces wholesale.
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = data_root() / db_path
    directory = db_path.parent / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@router.post("/run-code", response_model=RunCodeResponse)
async def run_code(request: RunCodeRequest) -> RunCodeResponse:
    """Execute a model-written chart script the user explicitly approved.

    Nothing reaches this endpoint without a click: the UI shows the code and a
    Run button. Validation still happens server-side, since an endpoint is not
    made safe by the fact that a button usually calls it.
    """
    data_files: dict[str, bytes] = {}
    if request.conversation_id:
        data_files = await _collect_data_files(request.conversation_id)

    try:
        result = await code_runner.run_chart_code(request.code, data_files)
    except code_runner.CodeRejected as exc:
        return RunCodeResponse(ok=False, error=str(exc))

    return RunCodeResponse(
        ok=result.ok,
        stdout=result.stdout,
        error=result.error,
        image_base64=result.image_base64,
        duration_s=round(result.duration_s, 2),
    )


@router.post("/{conversation_id}", response_model=UploadResponse)
async def upload(
    conversation_id: str,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    """Accept files for one conversation and start indexing them."""
    exists = await asyncio.to_thread(conversations.conversation_exists, conversation_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Please upload at most {MAX_FILES_PER_UPLOAD} files at a time.",
        )

    accepted = []
    rejected: list[ExcludedModel] = []

    for upload_file in files:
        filename = Path(upload_file.filename or "file").name
        kind = extraction.detect_kind(filename)

        if kind == "unsupported":
            rejected.append(
                ExcludedModel(
                    name=filename,
                    reason="Unsupported file type. PDF, Word, Excel, CSV, text and images are supported.",
                )
            )
            continue

        data = await upload_file.read()
        if not data:
            rejected.append(ExcludedModel(name=filename, reason="The file is empty."))
            continue
        if len(data) > MAX_FILE_BYTES:
            rejected.append(
                ExcludedModel(
                    name=filename,
                    reason=f"Larger than the {MAX_FILE_BYTES // (1024 * 1024)}MB limit.",
                )
            )
            continue

        record = await asyncio.to_thread(
            store.create_attachment,
            conversation_id,
            filename,
            kind,
            upload_file.content_type,
            len(data),
            None,
        )

        # Images and tabular files keep their bytes: images are re-sent to the
        # model on later turns, and spreadsheets are handed to the chart sandbox
        # so generated pandas code reads the real file. Prose documents do not -
        # once chunked and embedded, the original adds nothing.
        if kind in {"image", "table"}:
            # Named by attachment id, so two uploads of "chart.png" cannot
            # overwrite each other.
            target = _uploads_dir() / f"{record.id}{Path(filename).suffix.lower()}"
            await asyncio.to_thread(target.write_bytes, data)
            await asyncio.to_thread(store.set_stored_path, record.id, str(target))

        accepted.append(record)
        # Indexing continues after the response is sent.
        background.add_task(rag.ingest, record.id, filename, data)

    return UploadResponse(attachments=accepted, rejected=rejected)


@router.get("/file/{attachment_id}")
async def get_file(attachment_id: str) -> FileResponse:
    """Serve a stored image back, for the thumbnail in the transcript."""
    stored_path, mime_type = await asyncio.to_thread(store.get_stored_path, attachment_id)
    if not stored_path:
        raise HTTPException(status_code=404, detail="No file stored for this attachment")
    path = Path(stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="The stored file is missing")
    return FileResponse(path, media_type=mime_type or "application/octet-stream")


@router.get("/{conversation_id}", response_model=AttachmentsListResponse)
async def list_for_conversation(conversation_id: str) -> AttachmentsListResponse:
    """Attachments plus their indexing status, polled by the UI while pending."""
    records = await asyncio.to_thread(store.list_attachments, conversation_id)
    return AttachmentsListResponse(attachments=records)


@router.delete("/{attachment_id}")
async def delete(attachment_id: str) -> dict:
    """Remove an attachment and its chunks, plus its file if it had one."""
    stored_path = await asyncio.to_thread(store.delete_attachment, attachment_id)
    if stored_path is None:
        record = await asyncio.to_thread(store.get_attachment, attachment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
    elif stored_path:
        try:
            await asyncio.to_thread(Path(stored_path).unlink, True)
        except OSError:
            logger.debug("could not delete %s", stored_path, exc_info=True)
    return {"deleted": True, "id": attachment_id}


async def _collect_data_files(conversation_id: str) -> dict[str, bytes]:
    """Read this conversation's spreadsheets so the sandbox can open them.

    Scoped to one conversation, which is what keeps generated code from reaching
    a file the user uploaded in a different chat.
    """
    entries = await asyncio.to_thread(store.list_table_files, conversation_id)

    files: dict[str, bytes] = {}
    for filename, stored_path in entries:
        path = Path(stored_path)
        try:
            files[filename] = await asyncio.to_thread(path.read_bytes)
        except OSError:
            # A file removed underneath us should not fail the whole run; the
            # code simply will not find it, and the error names the file.
            logger.debug("could not read %s for the sandbox", stored_path, exc_info=True)
    return files
