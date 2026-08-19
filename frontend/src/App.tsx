/**
 * Top-level shell: persistent sidebar + chat, with Manage Models as a
 * floating overlay on top rather than a view swap - the chat underneath
 * stays fully mounted (scroll position, draft text, live stream) and
 * closing the overlay returns to it exactly as it was. Onboarding
 * (scan -> choose -> download) runs once, only when no models are installed
 * yet, and is not part of the permanent shell.
 */
import { useCallback, useEffect, useState } from 'react'

import { getInstalledModels } from './api/client'
import { ChatScreen } from './components/ChatScreen'
import { HealthGate } from './components/HealthGate'
import { ManageModelsOverlay } from './components/ManageModelsOverlay'
import { OnboardingFlow } from './components/OnboardingFlow'
import { Sidebar } from './components/Sidebar'
import { useConversation } from './hooks/useConversation'
import { useConversations } from './hooks/useConversations'
import { useHealth } from './hooks/useHealth'
import { useModelPreload } from './hooks/useModelPreload'
import { useModelPull } from './hooks/useModelPull'
import type { InstalledModel } from './types/api'

type View = 'onboarding' | 'chat'

export function App() {
  const { health, loading: healthLoading, error: healthError, recheck } = useHealth()
  const ollamaReady = health?.ollama.running === true

  const [view, setView] = useState<View>('onboarding')
  const [installed, setInstalled] = useState<InstalledModel[]>([])
  const [installedChecked, setInstalledChecked] = useState(false)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [defaultModel, setDefaultModel] = useState<string | null>(null)
  const [manageModelsOpen, setManageModelsOpen] = useState(false)

  const conversations = useConversations(ollamaReady && view !== 'onboarding')
  const { conversation } = useConversation(activeConversationId)
  const backgroundPull = useModelPull()

  // Owned here, not inside ChatScreen, so the loaded model persists across
  // New Chat / reopening a conversation / visiting Manage Models - it only
  // reconnects when the user actually picks a different model, and stays
  // connected for the lifetime of the app (until the app itself closes).
  const modelPreload = useModelPreload()

  const refreshInstalled = useCallback(() => {
    getInstalledModels()
      .then((result) => setInstalled(result.models))
      .catch(() => setInstalled([]))
  }, [])

  // Decide the startup view once: onboarding if nothing is installed yet,
  // otherwise straight into chat with a sensible default model.
  useEffect(() => {
    if (!ollamaReady || installedChecked) return

    getInstalledModels()
      .then((result) => {
        setInstalled(result.models)
        if (result.models.length === 0) {
          setView('onboarding')
        } else {
          setView('chat')
        }
        setInstalledChecked(true)
      })
      .catch(() => {
        setInstalled([])
        setInstalledChecked(true)
      })
  }, [ollamaReady, installedChecked])

  // Resolve the default model once conversations are known: most-recently-used
  // model from history, falling back to the first installed model. Must wait
  // for the conversations fetch to actually settle at least once - resolving
  // while it's still pending (conversations.conversations is transiently [])
  // locked this in permanently to the fallback instead of the real last-used
  // model, since `defaultModel` becomes truthy and this effect never re-runs.
  useEffect(() => {
    if (defaultModel || installed.length === 0 || !conversations.hasLoadedOnce) return
    const mostRecent = conversations.conversations.find((c) => c.last_model)?.last_model
    setDefaultModel(mostRecent ?? installed[0]?.name ?? null)
  }, [conversations.conversations, conversations.hasLoadedOnce, defaultModel, installed])

  // The model the next message will use. Fully owned here (not inside
  // ChatScreen) so both the in-chat switcher and Manage Models' "Use this
  // model" button can change it and stay in sync with each other.
  const [currentModel, setCurrentModel] = useState<string | null>(null)

  useEffect(() => {
    if (!currentModel && defaultModel) setCurrentModel(defaultModel)
  }, [currentModel, defaultModel])

  // Tracks whether the *current* activeConversationId reflects a real
  // navigation (new chat / reopen from sidebar) or this same conversation
  // simply having just been adopted mid-send. ChatScreen uses this, keyed via
  // its `key` prop, to remount only on genuine navigation - never on adoption,
  // which must not reset the model the user is actively mid-conversation with.
  const [navigationKey, setNavigationKey] = useState(0)

  const handleOnboardingComplete = useCallback(
    (model: string) => {
      refreshInstalled()
      setDefaultModel(model)
      setCurrentModel(model)
      setView('chat')
    },
    [refreshInstalled],
  )

  const handleNewChat = useCallback(() => {
    setActiveConversationId(null)
    // Deliberately does NOT reset currentModel: New Chat keeps whichever
    // model is already loaded and in use, so the connection persists across
    // chats instead of reconnecting to the original startup default every
    // time. Only reopening a conversation with a different last_model, or an
    // explicit switcher/Manage Models pick, should change it.
    setNavigationKey((n) => n + 1)
    setView('chat')
    setManageModelsOpen(false)
  }, [])

  const handleOpenConversation = useCallback(
    (id: string) => {
      setActiveConversationId(id)
      // The conversation's own last_model is applied once its detail has
      // loaded (see the effect below) - this covers the interim frame with
      // last render's model rather than a wrong flash of `defaultModel`.
      setNavigationKey((n) => n + 1)
      setView('chat')
      setManageModelsOpen(false)
    },
    [],
  )

  // Once a freshly-opened conversation's detail arrives, adopt its last_model
  // as the current model - but only for the navigation that just requested
  // it (guarded by navigationKey), so a background refetch of the same
  // conversation later doesn't stomp a mid-chat model switch. A conversation
  // with no last_model yet (empty) falls back to whatever model is already
  // loaded (read via the setter's callback form, not as a dependency, so this
  // effect only re-runs on real navigation) rather than the stale startup
  // default - keeping the connection alive instead of reconnecting.
  const [modelSyncedForKey, setModelSyncedForKey] = useState(-1)
  useEffect(() => {
    if (modelSyncedForKey === navigationKey) return
    if (activeConversationId === null) {
      setModelSyncedForKey(navigationKey)
      return
    }
    if (conversation && conversation.id === activeConversationId) {
      setCurrentModel((loaded) => conversation.last_model ?? loaded ?? defaultModel)
      setModelSyncedForKey(navigationKey)
    }
  }, [activeConversationId, conversation, defaultModel, modelSyncedForKey, navigationKey])

  const handleConversationCreated = useCallback(
    (id: string) => {
      setActiveConversationId(id)
      conversations.refresh()
    },
    [conversations],
  )

  const handleUseModel = useCallback((modelName: string) => {
    setCurrentModel(modelName)
    setManageModelsOpen(false)
  }, [])

  const handleModelsChanged = useCallback(() => {
    refreshInstalled()
  }, [refreshInstalled])

  // A model picked from live library search (not yet downloaded) needs a
  // download before it can become the active model - reuse the same pull
  // hook Manage Models' cards use, just anonymously here.
  const handleDownloadByName = useCallback(
    (modelName: string) => {
      backgroundPull.start(modelName)
    },
    [backgroundPull],
  )

  useEffect(() => {
    if (backgroundPull.state === 'done') {
      refreshInstalled()
    }
  }, [backgroundPull.state, refreshInstalled])

  if (view === 'onboarding') {
    return (
      <HealthGate
        health={health}
        loading={healthLoading}
        error={healthError}
        onRetry={recheck}
      >
        <OnboardingFlow onComplete={handleOnboardingComplete} />
      </HealthGate>
    )
  }

  return (
    <HealthGate
      health={health}
      loading={healthLoading}
      error={healthError}
      onRetry={recheck}
    >
      <div className="shell">
        <Sidebar
          conversations={conversations.conversations}
          activeId={activeConversationId}
          onNewChat={handleNewChat}
          onOpenConversation={handleOpenConversation}
          onRenameConversation={conversations.renameConversation}
          onDeleteConversation={(id) => {
            conversations.deleteConversation(id)
            if (id === activeConversationId) setActiveConversationId(null)
          }}
          onOpenManageModels={() => setManageModelsOpen(true)}
          managingModels={manageModelsOpen}
        />

        <main className="shell-main">
          {currentModel && (
            <ChatScreen
              key={navigationKey}
              conversationId={activeConversationId}
              conversation={conversation}
              currentModel={currentModel}
              onModelChange={setCurrentModel}
              installed={installed}
              preloadState={modelPreload.state}
              preloadError={modelPreload.error}
              preloadTargetModel={modelPreload.targetModel}
              ensureModelLoaded={modelPreload.ensureLoaded}
              onConversationCreated={handleConversationCreated}
              onOpenManageModels={() => setManageModelsOpen(true)}
            />
          )}
        </main>

        {manageModelsOpen && (
          <ManageModelsOverlay
            installed={installed}
            activeModel={currentModel}
            onUse={handleUseModel}
            onModelsChanged={handleModelsChanged}
            onDownloadByName={handleDownloadByName}
            onClose={() => setManageModelsOpen(false)}
          />
        )}
      </div>
    </HealthGate>
  )
}
