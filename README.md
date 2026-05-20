# Video Competitor Intelligence

A Flask web app that accepts your company name and up to 4 competitor names, fetches publicly available YouTube and video metadata, generates a comparative analysis, and produces a downloadable PowerPoint report (.pptx).

## Setup

```powershell
cd "e:\video competitor intelligence"
C:/Python314/python.exe -m pip install -r requirements.txt
```

## Run locally

```powershell
C:/Python314/python.exe app.py
```

Then open `http://127.0.0.1:5000`.

## Public URL (local tunnel)

If you want a temporary public URL for sharing, install ngrok or use pyngrok:

```powershell
C:/Python314/python.exe -m pyngrok http 5000
```

Then paste the generated public URL into a browser.
