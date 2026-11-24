# Cortex frontend

Next.js 16 (App Router, Turbopack, Cache Components) · TypeScript · Tailwind CSS 4 · shadcn/ui-style
components on Radix · react-markdown.

## How it talks to the backend

The browser never holds the JWT. `/api/auth/{login,register,logout}` exchange credentials with
the FastAPI backend and keep the token in an `httpOnly`, `SameSite=Lax` cookie;
`/api/backend/*` forwards authenticated requests (including the SSE event stream and PDF
downloads) to `BACKEND_URL/api/v1/*`. Because everything is same-origin, the native
`EventSource` works with the cookie and the CSP keeps `connect-src 'self'`. `proxy.ts`
redirects signed-out visitors away from app routes.

## Develop

```bash
cp .env.example .env.local      # BACKEND_URL=http://localhost:8000
npm install
npm run dev                     # http://localhost:3000
```

No API keys? Run the backend with scripted agents: `cd ../backend && .venv/bin/python
../scripts/dev_fake_backend.py`. Everything is real except the LLM and the web.

API types are generated from the backend's OpenAPI schema:
`../scripts/gen_api_types.sh` → `types/api.ts` (aliases in `types/index.ts`).

## Checks

```bash
npx tsc --noEmit && npm run lint && npm run build
```
