# Forge web console (Next.js 14, standalone output). BUILD CONTEXT = repo root, because
# this is a pnpm workspace (apps/web + packages/*). Build with:
#   docker build -f apps/web/Dockerfile -t forge-web .
FROM node:22-alpine AS deps
WORKDIR /repo
RUN corepack enable
# Lockfile + workspace manifests first for cached installs.
COPY pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages ./packages
RUN pnpm install --frozen-lockfile || pnpm install

FROM node:22-alpine AS builder
WORKDIR /repo
RUN corepack enable
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY . .
# Clear any stale incremental cache (see memory: stale .next can break the build).
# NEXT_OUTPUT=standalone emits the self-contained server bundle (symlinks work on Linux).
ENV NEXT_OUTPUT=standalone
RUN rm -rf apps/web/.next && pnpm --filter web build

FROM node:22-alpine AS runner
WORKDIR /repo
ENV NODE_ENV=production
RUN addgroup -g 10002 nodejs && adduser -u 10002 -G nodejs -S nextjs
# Standalone bundle preserves the workspace layout (apps/web/server.js).
COPY --from=builder /repo/apps/web/.next/standalone ./
COPY --from=builder /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder /repo/apps/web/public ./apps/web/public
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "apps/web/server.js"]
