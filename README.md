# Creative Request

Versão experimental/nova interface do Larroudé Creative Performance Dashboard.

**Origem:** fork do `creative-dashboard-larroude` em 2026-05-13 (Production v3).

## Setup

Este repositório roda em paralelo ao `creative-dashboard-larroude` (versão estável). Use para experimentar nova interface sem afetar o dashboard live.

- **Hosting:** Vercel (URL própria a configurar)
- **Pipeline:** GitHub Actions cron diário (mesmo do original)
- **APIs:** Meta Marketing API v21 + Shopify Admin API 2025-01 + Google Sheets via Apps Script
- **Planilha de requests:** mesma (US Request - Meta tab) — `1GLgNjLzCqNdRyhB5V8lCh5LDT2VSiVnyXUJZQ0zpUss`

## Estrutura

```
build_dashboard_v4.py     # Render do dashboard (a ser reformulado)
rebuild_v5.py             # Análise: Pareto, copy levels, destinations, scoring
fetch_meta.py             # Meta API client
fetch_shopify.py          # Shopify API client
run_pipeline.py           # Orquestrador
refresh_meta_token.py     # Token refresh (60d expiry)
apps_script.gs            # Google Apps Script endpoint
pipeline/                 # Cópia dos scripts
.github/workflows/        # GitHub Actions
```

## Status atual (herda do production-v3)

- Dashboard com Top 15 products, max 5 ads/campanha, GAPs removido, Top URLs
- Send to Sheet 100% funcional (Apps Script V10)
- Pause = link manual para Meta Ads Manager
- TAB_NAME = "US Request - Meta"

## Próximos passos
- Reformular interface
