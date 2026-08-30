# Local research data

`data/alpaca/` is the local-only store for immutable Alpaca research evidence.
It is ignored by Git because it contains raw provider responses and large
Parquet files. Keep each collection in this layout:

```text
data/alpaca/collections/<collection-id>/
  underlying/                 # base manifest, raw/split bars, calendar, contracts
  option_observations/<name>/ # request manifest, raw/normalized option data, manifest
  interrupted/                # preserved incomplete attempts and failure records
```

Never overwrite a finalized manifest or combine files across collection IDs.
Research code must use the manifest hashes, not ambient paths, as lineage.
