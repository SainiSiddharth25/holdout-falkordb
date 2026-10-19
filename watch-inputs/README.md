# Stage 6 local watch folder

The pair `train.jsonl` and `eval.jsonl` is polled read-only. Each JSONL line is one record with stable `id` and `content` (or `code`/`text`) fields. Use separate, versioned copies to avoid accidental writes while investigating. Only human-approved **Promote reviewed snapshot** imports updated content; scanning never mutates the graph. Limit: 4 MiB per file, 5,000 records per split. These bundled examples are synthetic. Do not put credentials in this directory.
