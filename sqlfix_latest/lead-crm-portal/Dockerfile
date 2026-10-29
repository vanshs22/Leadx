FROM node:20-alpine

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG API_UPSTREAM=http://api:8000
ARG NEXT_PUBLIC_SUPABASE_URL=
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY=
ARG NEXT_PUBLIC_LEADX_LOCAL_AUTH=0
ENV API_UPSTREAM=${API_UPSTREAM} \
    NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL} \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY} \
    NEXT_PUBLIC_LEADX_LOCAL_AUTH=${NEXT_PUBLIC_LEADX_LOCAL_AUTH}

RUN npm run build && npm prune --omit=dev

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs \
    && chown -R nextjs:nodejs /app
USER nextjs

EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/health', { cache: 'no-store' }).then((response) => { if (!response.ok) process.exit(1); }).catch(() => process.exit(1))"]
CMD ["npm", "start"]
