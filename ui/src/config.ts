// UI settings from Vite env variables (ui/.env.local; see ui/.env.example).
// The token is bundled into the page, so this setup is for local, single-user use only.
export const agentApiConfig = {
  baseUrl: import.meta.env.VITE_AGENT_API_URL ?? 'http://localhost:8000',
  apiToken: import.meta.env.VITE_AGENT_API_TOKEN || undefined,
}
