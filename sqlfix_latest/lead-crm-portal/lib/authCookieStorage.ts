// Auth session storage intentionally stays memory-only.
// Persistent auth is held by the backend in HttpOnly cookies; JavaScript cannot read it.
let memory: Record<string, string> = {};
export const authCookieStorage = {
  getItem: async (key: string) => memory[key] ?? null,
  setItem: async (key: string, value: string) => { memory[key] = value; },
  removeItem: async (key: string) => { delete memory[key]; },
};
