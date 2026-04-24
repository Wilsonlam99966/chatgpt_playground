# chatgpt_playground

Technical SEO auditor MVP crawler.

## Run

```bash
python3 seo_crawler.py https://example.com --max-urls 20000 --output seo_report.json
```

## Output

The crawler writes a JSON report with:

- `summary`: crawled pages, issue counts, health score
- `pages`: per-page technical metadata
- `issues`: detected SEO issues with severity and code

## Feasibility check for 20,000 URLs

The crawler now defaults to `--max-urls 20000`.

You can check an estimate of runtime and transfer volume before crawling:

```bash
python3 seo_crawler.py https://example.com --max-urls 20000 --feasibility-only
```

This estimate is based on simple assumptions and helps you decide whether to split the crawl.

## Crawl your own website

Yes. Replace the URL with your own domain:

```bash
python3 seo_crawler.py https://yourdomain.com --max-urls 20000 --output yourdomain_report.json
```

The script runs as plain Python, so it works both:

- in Codex/dev container environments
- on your own machine (outside Codex) as long as Python 3 is installed
