# chatgpt_playground

Technical SEO auditor MVP crawler.

## Run

```bash
python3 seo_crawler.py https://example.com --max-urls 50 --output seo_report.json
```

## Output

The crawler writes a JSON report with:

- `summary`: crawled pages, issue counts, health score
- `pages`: per-page technical metadata
- `issues`: detected SEO issues with severity and code
