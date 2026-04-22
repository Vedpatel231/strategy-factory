from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8787"
OUT_DIR = Path("/tmp/strategy_factory_dashboard_qa")
PAGES = [
    "overview",
    "portfolio",
    "alpaca-live",
    "quantum",
    "bots",
    "performance",
    "learning",
    "regime",
    "decisions",
]
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1100},
    "mobile": {"width": 390, "height": 844},
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    console = []
    page_errors = []
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp_name, viewport in VIEWPORTS.items():
            context = browser.new_context(viewport=viewport)
            page = context.new_page()
            page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.goto(BASE_URL, wait_until="networkidle")
            for name in PAGES:
                page.evaluate("name => showPage(name)", name)
                page.wait_for_timeout(1800)
                shot = OUT_DIR / f"{vp_name}-{name}.png"
                page.screenshot(path=str(shot), full_page=True)
                flags = page.evaluate(
                    """() => {
                      const bodyWide = document.documentElement.scrollWidth > window.innerWidth + 2;
                      const active = document.querySelector('.page.active');
                      const wide = [];
                      const tableOverflows = [];
                      if (active) {
                        active.querySelectorAll('*').forEach((el) => {
                          const r = el.getBoundingClientRect();
                          if (r.width > window.innerWidth + 2 && !el.closest('.table-wrap')) {
                            wide.push({tag: el.tagName, cls: el.className, width: Math.round(r.width)});
                          }
                        });
                        active.querySelectorAll('.table-wrap').forEach((el) => {
                          if (el.scrollWidth > el.clientWidth + 2) tableOverflows.push({w: el.scrollWidth, c: el.clientWidth});
                        });
                      }
                      const manualButtons = Array.from(document.querySelectorAll('button')).filter((b) => {
                        const txt = (b.textContent || '').toLowerCase();
                        return active && active.contains(b) && /quick trade|close all|buy|sell/.test(txt);
                      }).map((b) => b.textContent.trim());
                      return {bodyWide, wideCount: wide.length, wide: wide.slice(0, 8), tableOverflows, manualButtons};
                    }"""
                )
                results.append((vp_name, name, flags))
            context.close()
        browser.close()

    print(f"QA screenshots: {OUT_DIR}")
    print(f"Console warnings/errors: {len(console)}")
    for line in console[:30]:
        print("CONSOLE", line[:300])
    print(f"Page errors: {len(page_errors)}")
    for line in page_errors[:20]:
        print("PAGEERROR", line[:300])
    print("Page layout results:")
    for vp_name, name, flags in results:
        problems = []
        if flags["bodyWide"]:
            problems.append("body-wide")
        if flags["wideCount"]:
            problems.append(f"wide-elements={flags['wideCount']}")
        if flags["tableOverflows"]:
            problems.append(f"table-overflows={len(flags['tableOverflows'])}")
        if flags["manualButtons"]:
            problems.append(f"manual-buttons={flags['manualButtons']}")
        print(f"  {vp_name:7} {name:12} {'; '.join(problems) if problems else 'ok'}")
        for item in flags["wide"]:
            print(f"    wide {item}")


if __name__ == "__main__":
    main()
