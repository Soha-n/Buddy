/** TypeScript mirrors of the backend Pydantic schemas in app/models/schemas.py. */

export type FitLevel = 'excellent' | 'good' | 'tight'

export interface CpuInfo {
  name: string
  physical_cores: number | null
  logical_cores: number | null
  max_clock_mhz: number | null
  architecture: string
}

export interface GpuInfo {
  name: string
  vram_gb: number | null
  vram_free_gb: number | null
  driver_version: string | null
  vendor: string
  source: string
  /** False when VRAM came from AdapterRAM, which saturates at 4 GB. */
  vram_reliable: boolean
}

export interface RamInfo {
  total_gb: number
  available_gb: number | null
}

export interface DiskInfo {
  path: string
  total_gb: number
  free_gb: number
}

export interface OsInfo {
  system: string
  release: string
  version: string
  machine: string
}

export interface SystemSpecs {
  cpu: CpuInfo
  ram: RamInfo
  gpus: GpuInfo[]
  disk: DiskInfo
  os: OsInfo
  warnings: string[]
  detected_at: string
}

export interface CatalogModel {
  id: string
  name: string
  family: string
  params_b: number
  download_size_gb: number
  min_ram_gb: number
  recommended_ram_gb: number
  min_vram_gb: number
  quality_tier: number
  strengths: string[]
  description: string
  is_default: boolean
  tags: string[]
}

export interface Recommendation {
  model: CatalogModel
  score: number
  fit: FitLevel
  reasoning: string[]
  expected_speed: string
  gpu_offload: boolean
}

export interface ExcludedModel {
  name: string
  reason: string
}

export interface RecommendationsResponse {
  specs: SystemSpecs
  recommendations: Recommendation[]
  excluded: ExcludedModel[]
  /** Total catalog size, so the UI can report how many models were ranked. */
  catalog_size: number
}

export interface OllamaStatus {
  installed: boolean
  running: boolean
  version: string | null
  binary_path: string | null
  error: string | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  ollama: OllamaStatus
}

export interface InstalledModel {
  name: string
  size_bytes: number
  modified_at: string | null
}

export interface InstalledModelsResponse {
  models: InstalledModel[]
}

export type ChatRole = 'system' | 'user' | 'assistant'

export interface ChatMessage {
  role: ChatRole
  content: string
  /** base64 image payloads, only ever sent to a vision-capable model. */
  images?: string[]
}

export type ModelTier = 'best' | 'better' | 'good'

export interface TieredRecommendation extends Recommendation {
  tier: ModelTier
}

export interface TiersResponse {
  specs: SystemSpecs
  best: TieredRecommendation[]
  better: TieredRecommendation[]
  good: TieredRecommendation[]
  excluded: ExcludedModel[]
  catalog_size: number
}

export interface LibraryEntry {
  name: string
  description: string | null
  size_hint: string | null
}

export interface LibrarySearchResponse {
  query: string
  entries: LibraryEntry[]
  source: 'live' | 'stale_cache' | 'catalog_only'
  stale: boolean
}

/* ------------------------------- Conversations -------------------------------- */

export interface MessageRecord {
  id: number
  role: ChatRole
  content: string
  created_at: string
  model_used_for_this_turn: string | null
  attachments?: AttachmentRecord[]
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  last_model: string | null
}

export interface ConversationDetail extends ConversationSummary {
  messages: MessageRecord[]
}

export interface ConversationsListResponse {
  conversations: ConversationSummary[]
}

/* ----------------------------- SSE event payloads ---------------------------- */

export interface PullProgress {
  status: string
  digest: string | null
  total: number
  completed: number
  percent: number | null
  speed_bps: number | null
  eta_s: number | null
}

export interface PullDone {
  model: string
}

export interface ChatMeta {
  conversation_id: string
}

export interface ChatToken {
  content: string
}

export interface ChatDone {
  eval_count: number
  tokens_per_sec: number | null
  done_reason: string | null
}

export interface StreamError {
  message: string
}

/** Discriminated union of every SSE frame the backend emits. */
export type SseEvent =
  | { event: 'progress'; data: PullProgress }
  | { event: 'meta'; data: ChatMeta }
  | { event: 'sources'; data: ChatSources }
  | { event: 'token'; data: ChatToken }
  | { event: 'done'; data: PullDone | ChatDone }
  | { event: 'error'; data: StreamError }

/* -------------------------------- Attachments -------------------------------- */

export type AttachmentKind = 'pdf' | 'docx' | 'table' | 'text' | 'image' | 'unsupported'
export type AttachmentStatus = 'pending' | 'ready' | 'error'

export interface AttachmentRecord {
  id: string
  conversation_id: string
  filename: string
  kind: AttachmentKind
  mime_type: string | null
  size_bytes: number
  created_at: string
  status: AttachmentStatus
  error: string | null
  chunk_count: number
  /** Images only: a vision description exists, so text-only models can use it. */
  has_description: boolean
}

export interface AttachmentsListResponse {
  attachments: AttachmentRecord[]
}

export interface UploadResponse {
  attachments: AttachmentRecord[]
  /** Reuses {name, reason} from ExcludedModel. */
  rejected: ExcludedModel[]
}

/* ------------------------------- Capabilities -------------------------------- */

export interface VisionCheckResponse {
  model: string
  supports_vision: boolean
  installed_vision_models: string[]
}

/* --------------------------------- Chart code -------------------------------- */

export interface RunCodeResponse {
  ok: boolean
  stdout: string
  error: string | null
  image_base64: string | null
  duration_s: number
}

/* -------------------------------- Web search --------------------------------- */

export interface WebSearchStatus {
  available: boolean
  /** 'searxng' | 'duckduckgo' | 'none' */
  provider: string
  searxng_detected: boolean
  detail: string | null
}

export interface SearchCitation {
  index: number
  title: string
  url: string
  /** True when the full page was read, not just the search snippet. */
  fetched: boolean
}

export interface ChatSources {
  query: string
  provider: string
  citations: SearchCitation[]
}

/* ------------------------------- User context -------------------------------- */

export interface LocationResponse {
  city: string | null
  region: string | null
  country: string | null
  timezone: string | null
  label: string
  /** How it was determined: typed by the user, the OS location service, OS
   *  regional settings (country only), or not known. */
  source: 'manual' | 'os_gps' | 'os_region' | 'unavailable'
  /** False where the OS cannot give coordinates, so the UI hides that option. */
  precise_available: boolean
  local_date: string
  local_time: string
}
