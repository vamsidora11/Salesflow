# Simulator

`simulate_sales.py` posts realistic batched sale events to the ingestion API. Standard library only.

```bash
# 1. create demo tenants/stores/products and write credentials to ../.seed_output.json
docker compose exec api python scripts/seed_demo_data.py   # add --reset to re-seed

# 2a. run on the host against nginx
python simulator/simulate_sales.py --rate 200 --duration 120

# 2b. or as a container (profile "demo"; reads the seed file via a bind mount)
docker compose --profile demo up simulator
```

Options: `--rate` (events/s), `--stores N`, `--tenant "Acme Retail"`, `--duration S`,
`--no-daily-pattern` (flat rate, for load tests), `--seed`.

Realism: weekday-style traffic with lunch (~12:30) and evening (~18:30) peaks scaled by `--rate`,
a Zipf-skewed product mix, 1-3 line items per sale, ~10% discounted prices, and a 60/30/10
pos/online/mobile channel split. It honours `Retry-After` on 429/503.
