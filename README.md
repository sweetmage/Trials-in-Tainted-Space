sourceTiTS
==========

Trials in Tainted Space


Documentation will eventually be here: http://fenoxo.github.io/sourceTiTS

## Read-only narrative PDF export

To strip out gameplay elements and produce a linear, read-only PDF from the source dialogue and image references, run:

```bash
python3 devTools/export_readonly_pdf.py --output exports/tits_read_only_dialogue.pdf
```

This export collects static text passed to `output(...)` and image identifiers passed to `showImage(...)`, then compiles them into a single PDF document.
