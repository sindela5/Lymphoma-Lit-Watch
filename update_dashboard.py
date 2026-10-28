import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

JOURNALS = [
    "N Engl J Med", "Lancet", "Lancet Haematol", "Blood",
    "Blood Adv", "J Clin Oncol", "Ann Oncol", "Br J Haematol",
    "Haematologica", "Nat Med", "J Hematol Oncol"
]

PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DATA_FILE = "data/papers.json"

def fetch_pubmed_ids():
    journal_terms = " OR ".join([f'"{j}"[Journal]' for j in JOURNALS])
    query = f'({journal_terms}) AND (lymphoma[Title/Abstract] OR "Lymphoma"[Mesh]) AND ("last 30 days"[PDat])'
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": "50"
    }
    url = f"{PUBMED_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "LymphomaLitWatch/2.0"})
    
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("esearchresult", {}).get("idlist", [])

def fetch_paper_details(pmid_list):
    if not pmid_list:
        return []
    
    params = {
        "db": "pubmed",
        "id": ",".join(pmid_list),
        "retmode": "xml"
    }
    url = f"{PUBMED_FETCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "LymphomaLitWatch/2.0"})
    
    with urllib.request.urlopen(req) as resp:
        xml_data = resp.read().decode("utf-8")
    
    root = ET.fromstring(xml_data)
    papers = []

    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//MedlineCitation/PMID")
        
        # Title
        title_elem = article.find(".//ArticleTitle")
        title = "".join(title_elem.itertext()).strip() if title_elem is not None else "No title"
        
        # Journal
        journal = article.findtext(".//Journal/Title") or article.findtext(".//Journal/ISOAbbreviation") or "Unknown Journal"
        
        # Publication Date
        pub_date = article.find(".//JournalIssue/PubDate")
        year = pub_date.findtext("Year") if pub_date is not None and pub_date.findtext("Year") else str(datetime.now().year)
        month = pub_date.findtext("Month") if pub_date is not None and pub_date.findtext("Month") else "Jan"
        
        # DOI
        doi = None
        for id_elem in article.findall(".//ArticleId"):
            if id_elem.attrib.get("IdType") == "doi":
                doi = id_elem.text
                break

        # Abstract
        abstract_parts = article.findall(".//AbstractText")
        abstract_text = " ".join(["".join(part.itertext()) for part in abstract_parts]) if abstract_parts else ""

        if not abstract_text:
            continue

        papers.append({
            "pmid": pmid,
            "title": title,
            "journal": journal,
            "pub_date": f"{month} {year}",
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "abstract": abstract_text
        })
        
    return papers

def summarize_with_gemini(paper):
    if not GEMINI_API_KEY:
        return {
            "category": "Aggressive B-cell",
            "objective": "No API key configured.",
            "findings": ["Abstract available on PubMed."],
            "takeaway": "N/A"
        }

    prompt = f"""
    Analyze this PubMed lymphoma abstract and generate a JSON response with:
    1. "category": Must be strictly one of: ["Aggressive B-cell", "Indolent B-cell", "T-cell / NK-cell", "Other Lymphoma"]
    2. "objective": 1 sentence summarizing the primary objective.
    3. "findings": Array of 2 to 3 key findings or statistical results.
    4. "takeaway": 1 concise sentence summarizing clinical implications.

    Title: {paper['title']}
    Journal: {paper['journal']}
    Abstract: {paper['abstract']}
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            text = res_data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
    except Exception as e:
        print(f"Gemini API Error for PMID {paper['pmid']}: {e}")
        return {
            "category": "Other Lymphoma",
            "objective": "Failed to parse abstract automatically.",
            "findings": ["Refer to primary paper."],
            "takeaway": "Refer to primary publication."
        }

def main():
    os.makedirs("data", exist_ok=True)
    
    # Load existing papers
    existing_papers = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                existing_papers = json.load(f)
        except Exception:
            existing_papers = []

    existing_pmids = {p["pmid"] for p in existing_papers}
    
    pmids = fetch_pubmed_ids()
    new_pmids = [p for p in pmids if p not in existing_pmids]
    
    if not new_pmids:
        print("No new articles found.")
        return

    print(f"Found {len(new_pmids)} new articles.")
    new_articles = fetch_paper_details(new_pmids)
    
    for paper in new_articles:
        print(f"Processing PMID {paper['pmid']}...")
        summary = summarize_with_gemini(paper)
        paper.update(summary)
        paper["added_date"] = datetime.now().strftime("%Y-%m-%d")
        existing_papers.insert(0, paper)
        time.sleep(1)  # Respect rate limits

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(existing_papers, f, indent=2, ensure_ascii=False)
        
    print("Successfully updated paper database.")

if __name__ == "__main__":
    main()
