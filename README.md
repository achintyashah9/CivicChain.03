Every year, our municipal corporations publish hundreds of pages of budget PDFs filled with numbers, codes, and jargon. Citizens pay their taxes but rarely understand: Where is the money actually going? Why are some roads still broken despite funds being allocated? Why do some wards seem neglected while others get more projects?

CivicChain was built to answer these questions. We take those complex, inaccessible budget PDFs and turn them into something every citizen can use: -> Interactive dashboards, plain-language AI insights, and ward-level maps.

Because in a democracy, informed citizens are truly empowered citizens.

CivicChain was built to answer these questions.
We take those complex, inaccessible budget PDFs and turn them into something every citizen can use:
-> Interactive dashboards, plain-language AI insights, and ward-level maps.
    Because in a democracy, informed citizens are truly empowered citizens.
    
What CivicChain Does
Data Pipeline -> Automatically fetches municipal budget PDFs every year
AI Parsing & OCR -> Handles English + regional language documents
Database + Dashboard -> Clean, searchable data for departments and wards
AI Insights (Gemini) -> Detects anomalies (spikes/dips in spending) and explains them in simple words
GIS Map Integration -> See exactly where money was spent in your city

Who It’s For
Citizens -> To know how their taxes are used
Journalists & NGOs -> To investigate spending patterns
City Planners -> To track utilization and prevent misuse
Students/Researchers -> To study urban governance with clean datasets

Tech Stack
Frontend: React (Vite), Tailwind CSS, Recharts, Leaflet.js (maps)
Backend: Supabase (Postgres + Edge Functions + Auth)
AI Layer: Google Gemini API for anomaly detection & summaries
Parsing Pipeline: Python (pdfplumber, Camelot, Tesseract OCR) + Translation API
Hosting: Vercel (Frontend), Supabase Cloud (DB + Functions)

How It Works (High-Level)
Fetch -> Download yearly municipal budget PDFs automatically
Parse -> Convert PDFs (even in regional languages) into structured CSV
Load -> Store clean data in Supabase (city → wards → departments)
Analyze -> AI detects anomalies & explains trends in plain language
Visualize -> Citizens see interactive charts, maps, and summaries
Visualize -> Citizens see interactive charts, maps, and summaries
