#!/usr/bin/env bash
# Download the pinned sample 10-Ks used by the parser golden tests.
# Usage: scripts/fetch-sample-filings.sh [dir]   (default: ingest-go/testdata/filings, git-ignored)
set -euo pipefail
DIR="${1:-$(dirname "$0")/../ingest-go/testdata/filings}"
UA="${EDGAR_USER_AGENT:-FinRAG research you@example.com}"
mkdir -p "$DIR"
while read -r ticker url; do
  [ -s "$DIR/$ticker.htm" ] && continue
  curl -sS --fail --compressed -A "$UA" "$url" -o "$DIR/$ticker.htm"
  sleep 0.2
done <<'LIST'
AAPL https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm
MSFT https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm
GOOGL https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm
AMZN https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm
NVDA https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm
META https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm
JPM https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm
XOM https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm
LIST
echo "filings in $DIR"
