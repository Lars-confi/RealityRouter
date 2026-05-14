# RealityRouter docs — upload instructions

These markdown files are the source of truth for the website's docs at
`/realityrouter/docs/*`. The website fetches them from
`https://github.com/Lars-confi/RealityRouterTemp` at build time and renders them
inside the existing docs layout.

## What to upload

Push the `docs/` folder in this directory to the **root** of the
`RealityRouterTemp` repo. Final structure on GitHub:

```
RealityRouterTemp/
├── README.md            ← already there (becomes /realityrouter/docs root)
├── ARCHITECTURE.md      ← already there (becomes /realityrouter/docs/architecture)
└── docs/                ← NEW — add this folder
    ├── quickstart.md
    ├── concepts.md
    ├── routing.md
    ├── agents.md
    ├── api.md
    └── dashboard.md
```

## After upload

Within ~60 seconds of pushing, the website's docs will reflect the new content.
(In production, set up a GitHub webhook → Vercel deploy hook for instant rebuilds.)

## Conventions

- **Frontmatter** at the top of each file (`title`, `description`) is optional —
  if absent, the first `# H1` becomes the page title.
- **GitHub callouts** are supported: `> [!NOTE]`, `> [!INFO]`, `> [!WARNING]`.
- **Internal links** to other docs pages: use relative paths like
  `[Routing strategies](./routing.md)` — the website rewrites them to
  `/realityrouter/docs/routing`.
- **Code blocks** are highlighted by language (` ```python `, ` ```bash `, etc.).
- **Tables, task lists, strikethrough** all work (GitHub Flavored Markdown).

To add a new docs page later: just drop a new `.md` file into `docs/` and add an
entry to `lib/docs-fetch.ts` → `DOCS_CATALOG` in the website repo.
