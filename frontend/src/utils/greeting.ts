/** Varied welcome copy for the empty-chat state, once a model is connected. */

const FIRST_GREETING = (model: string) =>
  `Hey, I'm your private Buddy — running ${model} right here on your machine. What's on your mind?`

const GREETINGS: Array<(model: string) => string> = [
  FIRST_GREETING,
  (model: string) =>
    `Hi there. I'm private Buddy, powered by ${model} and never leaving this computer. Ask me anything.`,
  (model: string) =>
    `Hey! Private Buddy here, ready with ${model}. Nothing you say leaves this device — go ahead.`,
  (model: string) =>
    `Hello — I'm your private Buddy, running fully offline on ${model}. What can I help with?`,
]

/** Deterministic per-model pick, so the greeting doesn't reshuffle on every
 * render but still varies across models/sessions. */
export function greetingFor(model: string): string {
  let hash = 0
  for (let i = 0; i < model.length; i++) {
    hash = (hash * 31 + model.charCodeAt(i)) | 0
  }
  const index = Math.abs(hash) % GREETINGS.length
  const template = GREETINGS[index] ?? FIRST_GREETING
  return template(model)
}
