# Paper Build Notes

This folder contains the AAS-style manuscript drafts for Citizen Photometry.

## Build The PDF

From the repository root, run:

```powershell
python docs\paper\build_pdf.py
```

To build the asteroid/comet draft instead of the default photometry manuscript:

```powershell
python docs\paper\build_pdf.py --source citizen_asteroid_comet_paper.tex
```

To build and open the PDF immediately after success:

```powershell
python docs\paper\build_pdf.py --open
```

To force a clean rebuild:

```powershell
python docs\paper\build_pdf.py --clean
```

The generated PDF is written to:

```text
docs/paper/build/citizen_photometry_paper.pdf
```

For the asteroid/comet manuscript source above, the generated PDF is written to:

```text
docs/paper/build/citizen_asteroid_comet_paper.pdf
```

## Requirements

The build script looks for one of these local LaTeX toolchains:

- `latexmk`
- `pdflatex` plus `bibtex`

It also verifies that the AAS class requested by the manuscript, currently `aastex701`, is available through `kpsewhich` before trying to compile.

If no LaTeX tool is installed, or if the AAS class is missing, the script exits with a clear error message instead of failing silently.

For AAS-style compilation, a TeX distribution that includes the requested AAS class file is required.