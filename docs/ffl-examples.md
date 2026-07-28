# FFL Examples — `uspanel`

Every numbered scenario is a **complete, compilable FFL file**. Copy one into
`my.ffl` and run it:

```bash
fw ffl run --primary my.ffl \
  --library ~/fw_handlers/fwh_uspanel/src/uspanel/ffl/uspanel.ffl \
  --workflow my.uspanel.<WorkflowName>
```

A runner serving the `uspanel` namespace must be up
(`fw runner start --domain uspanel`), and the panel build needs `CENSUS_API_KEY`.
Every block below is compile-checked against the domain's FFL.

New to the language? Start with the
[FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md)
and the [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical).

---

## The facets at a glance

Two facets in a strict order: build the panel, then analyze it. That two-stage
shape — expensive ingest cached once, cheap analysis re-run many times — is the
pattern worth copying.

| Declaration | Signature | Does |
|---|---|---|
| `uspanel.data.BuildPanel` | `(force = false) => (csv_path, json_path, n_rows, year_min, year_max)` | Fetch every source, outer-join on (state, year), cache tidy CSV + JSON |
| `uspanel.data.BuildFindings` | `(force = false) => (html_path, n_rows, n_states)` | Analyze the cached panel → self-contained findings page |
| `uspanel.workflows.BuildUSStatePanel` | `(force = false) => (status, csv_path, n_rows)` | Shipped entry point for the panel |
| `uspanel.workflows.BuildUSPanelFindings` | `(force = false) => (status, html_path, n_rows)` | Shipped entry point for the findings page |

---

## 1. Run what ships — no FFL to write

```bash
fw ffl seed --include uspanel

fw ffl run --primary ~/fw_handlers/fwh_uspanel/src/uspanel/ffl/uspanel.ffl \
  --workflow uspanel.workflows.BuildUSStatePanel --inputs '{"force": false}'

fw ffl run --primary ~/fw_handlers/fwh_uspanel/src/uspanel/ffl/uspanel.ffl \
  --workflow uspanel.workflows.BuildUSPanelFindings
```

Write FFL when you want both stages in one submission, your own error handling, or
a guard on panel coverage before publishing findings.

## 2. Both stages in one workflow

Every FFL workflow needs a `namespace`, a `use` per namespace it calls into, and a
`yield` back to itself.

```ffl
namespace my.uspanel {

    use uspanel.data

    /** Build the panel, then the findings page over it. */
    workflow PanelAndFindings(force: Boolean = false) => (html_path: String, rows: Int) andThen {

        panel = uspanel.data.BuildPanel(force = $.force)

        findings = uspanel.data.BuildFindings(force = $.force)

        yield PanelAndFindings(html_path = findings.html_path, rows = findings.n_rows)
    }
}
```

> ⚠️ Careful: **line order is not run order**. Nothing in `findings` references
> `panel`, so the two may run concurrently and the analysis could read a stale
> panel. The next example fixes that.

Rules also visible above: `=>` sits on the **same line** as the closing `)`;
references are always `step.field`; `$.force` reads the workflow's parameter.

## 3. Make the order explicit

Ordering in FFL comes from **references**, not from layout. `BuildFindings` takes
no `dependency_signal` parameter, so express the order by branching off a value the
first step returns — here with a `when` that only runs the analysis once the panel
reports rows:

```ffl
namespace my.uspanel {

    use uspanel.data

    /** Analysis runs only after the panel step reported its row count. */
    workflow OrderedPanelRun(force: Boolean = false, min_rows: Int = 500) => (status: String, html_path: String) andThen {

        panel = uspanel.data.BuildPanel(force = $.force) andThen when {
            case $.n_rows >= $$.min_rows => {
                findings = uspanel.data.BuildFindings(force = $$.force)
                yield OrderedPanelRun(status = "completed", html_path = findings.html_path)
            }
            case _ => {
                yield OrderedPanelRun(status = "panel_too_thin", html_path = "")
            }
        }
    }
}
```

Inside the `when` cases, `$` is the **panel step** (so `$.n_rows` is its result)
and `$$` is the workflow (so `$$.force` is its parameter). Every `when` needs a
default case, and it must come last.

## 4. Call-time mixins — timeouts and retries

Both facets ship `with Timeout(minutes = 15)`. The **call site** can override for
one particular use — useful when the Census API is slow.

```ffl
namespace my.uspanel {

    use uspanel.data

    /** A full refresh across every source: more time, and retries. */
    workflow ResilientPanel() => (rows: Int) andThen {

        panel = uspanel.data.BuildPanel(force = true) with Timeout(minutes = 60) with Retry(maxAttempts = 3, backoffSeconds = 120)

        yield ResilientPanel(rows = panel.n_rows)
    }
}
```

## 5. Survive a failed source — `catch`

`catch` runs when its step errors after retries are exhausted (for example, a
missing `CENSUS_API_KEY`). Yielding from the catch block ends the run with a
partial result instead of a hard failure.

```ffl
namespace my.uspanel {

    use uspanel.data

    /** Report why the panel didn't build rather than failing opaquely. */
    workflow BestEffortPanel() => (status: String, csv_path: String) andThen {

        panel = uspanel.data.BuildPanel(force = true) catch {
            yield BestEffortPanel(status = "source_fetch_failed", csv_path = "")
        }

        yield BestEffortPanel(status = "completed", csv_path = panel.csv_path)
    }
}
```

## 6. Report the coverage window — expressions

`++` concatenates strings; step results are read by step name.

```ffl
namespace my.uspanel {

    use uspanel.data

    /** Build the panel and report the years it actually covers. */
    workflow PanelCoverage() => (coverage: String, rows: Int) andThen {

        panel = uspanel.data.BuildPanel()

        yield PanelCoverage(
            coverage = "state-year panel, " ++ panel.csv_path,
            rows = panel.n_rows)
    }
}
```

## 7. Reuse the shipped workflows

Workflows compose like facets — wrap them rather than forking them.

```ffl
namespace my.uspanel {

    use uspanel.workflows

    /** Wrap the shipped workflows and reshape the result. */
    workflow PanelWithHeadline() => (headline: String) andThen {

        built = uspanel.workflows.BuildUSStatePanel(force = false)

        yield PanelWithHeadline(headline = "panel: " ++ built.status)
    }
}
```

## 8. Compose across domains — publish the findings page

`census.Publish` is the generic publisher the map/report domains share.

```ffl
namespace my.uspanel {

    use uspanel.data
    use census.Publish

    /** Build the findings page, then push it to the public site. */
    workflow FindingsPublish(repo: String = "rlemke/facetwork-maps") => (pages_url: String) andThen {

        findings = uspanel.data.BuildFindings()

        published = census.Publish.PublishWebBundle(
            repo = $.repo,
            prefixes = ["uspanel/output"],
            dests = ["us/state-panel"],
            labels = ["US state-year panel findings"],
            landing_title = "Facetwork maps")

        yield FindingsPublish(pages_url = published.pages_url)
    }
}
```

Compile that one with `--library ~/fw_handlers/fwh_census_us/src/census_us/ffl/census.ffl`
as well.

---

## Cheat sheet

| You want to… | Write |
|---|---|
| Read a workflow/step parameter | `$.name` (`$$.name` one level out) |
| Read a previous step's result | `stepname.field` |
| Order two steps | reference a field of the first from the second (or gate with `when`) |
| More time / retries for one call | `… with Timeout(minutes = 60) with Retry(maxAttempts = 3, backoffSeconds = 120)` |
| Handle a step failure | `step = Facet(…) catch { yield … }` |
| Branch | `step = Facet(…) andThen when { case <bool> => { … } case _ => { … } }` |
| Fan out over a list | `workflow W(items: Json) … andThen foreach i in $.items { … }` |
| Concatenate strings | `a ++ b` |

**Validate before you run:** `afl my.ffl --check` or MCP `fw_validate`. Every error
carries a `rule_id` — fetch `fw://docs/rules/{rule_id}` for a wrong/right pair.

## See also

- [`docs/README.md`](README.md) — per-feature specs for this domain
- [FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md) ·
  [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical) ·
  [relative `$`-scoping](https://github.com/rlemke/facetwork/blob/main/docs/architecture/ffl-relative-scoping.md)
- The domain's FFL under `src/uspanel/ffl/` — the source of truth for every signature above
