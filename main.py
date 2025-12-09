"""
Pharmyrus API v7.0 - "Cortellis Killer"
Brazilian Pharmaceutical Patent Search with Deep Navigation

Features:
- Direct patent details via google_patents_details engine
- Full worldwide applications extraction (ALL countries)
- Complete patent metadata (title, abstract, claims, inventors, assignee, dates)
- Multiple fallback strategies
- EPO OPS API integration
"""

import asyncio
import httpx
import re
import os
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="Pharmyrus API",
    description="Brazilian Pharmaceutical Patent Search - v7.0 Cortellis Killer",
    version="7.0.0"
)

# ============================================================================
# API KEYS
# ============================================================================

SERPAPI_KEYS = [
    "3f22448f4d43ce8259fa2f7f6385222323a67c4ce4e72fcc774b43d23812889d",
    "bc20bca64032a7ac59abf330bbdeca80aa79cd72bb208059056b10fb6e33e4bc",
    "aad6d736889f91f9e7fe5a094336589404d04eda73fee9b158e328c2bd5a4d7e",
    "d3b97e647e940af0b9bb5d37605cae9a8f59c495e6764630da8477434be4ce81",
    "e4c1f53a45a2b6d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2"
]

class APIKeyRotator:
    def __init__(self):
        self.keys = SERPAPI_KEYS
        self.index = 0
        self.lock = asyncio.Lock()
    
    async def get_key(self) -> str:
        async with self.lock:
            key = self.keys[self.index % len(self.keys)]
            self.index += 1
            return key

api_keys = APIKeyRotator()

# EPO Credentials
EPO_KEY = "2aREwiCfxKYWNEVnhPD7S69yYdmr2pii"
EPO_SECRET = "gE4ImsCbhF0QF2pk"

# ============================================================================
# HTTP CLIENT WITH RETRY
# ============================================================================

async def http_get(url: str, params: dict = None, headers: dict = None, timeout: float = 45.0) -> dict:
    """HTTP GET with retry and rate limit handling"""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params, headers=headers)
                
                if response.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                
                if response.status_code == 403:
                    await asyncio.sleep(1)
                    continue
                    
                response.raise_for_status()
                return response.json()
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1)
                continue
            return {"error": str(e)}
    return {}

async def http_post(url: str, data: dict = None, headers: dict = None, timeout: float = 30.0) -> dict:
    """HTTP POST for EPO authentication"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, data=data, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception:
        return {}

# ============================================================================
# PUBCHEM SERVICE
# ============================================================================

async def get_pubchem_data(molecule: str, debug_log: list) -> dict:
    debug_log.append(f"[PubChem] Fetching: {molecule}")
    
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{molecule}/synonyms/JSON"
    data = await http_get(url)
    
    dev_codes = []
    cas = None
    synonyms = []
    iupac_names = []
    
    try:
        syns = data.get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
        
        dev_pattern = re.compile(r'^[A-Z]{2,5}[-]?\d{3,7}[A-Z]?$', re.IGNORECASE)
        cas_pattern = re.compile(r'^\d{2,7}-\d{2}-\d$')
        
        for s in syns[:200]:
            if dev_pattern.match(s) and len(dev_codes) < 30:
                dev_codes.append(s)
            if cas_pattern.match(s) and not cas:
                cas = s
            if len(s) > 3 and len(s) < 100:
                synonyms.append(s)
            if "(" in s and ")" in s and len(s) > 50:
                iupac_names.append(s)
        
        debug_log.append(f"[PubChem] Found {len(dev_codes)} dev codes, CAS: {cas or 'None'}, {len(synonyms)} synonyms")
    except Exception as e:
        debug_log.append(f"[PubChem] Error: {str(e)}")
    
    return {
        "dev_codes": dev_codes,
        "cas": cas,
        "synonyms": synonyms[:80],
        "iupac_names": iupac_names[:5]
    }

# ============================================================================
# WO EXTRACTION PATTERNS
# ============================================================================

def extract_wo_numbers(text: str) -> list:
    patterns = [
        r'WO[\s-]?(\d{4})[\s/]?(\d{6})',
        r'WO(\d{4})(\d{6})[A-Z]?\d?',
        r'WO\s?(\d{4})/(\d{6})',
        r'WO(\d{4})[\s-](\d{6})',
        r'PCT/[A-Z]{2}(\d{4})/(\d{6})',
    ]
    
    wo_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            if len(m) == 2:
                year = m[0]
                number = m[1].zfill(6)
                if len(year) == 2:
                    year = "20" + year if int(year) < 50 else "19" + year
                wo_numbers.add(f"WO{year}{number}")
    
    return list(wo_numbers)

# ============================================================================
# WO DISCOVERY - MULTI-SOURCE
# ============================================================================

async def discover_wo_numbers(molecule: str, brand: str, dev_codes: list, cas: str, synonyms: list, debug_log: list) -> list:
    debug_log.append("[WO Discovery] Starting comprehensive search")
    
    all_wo_numbers = set()
    search_queries = []
    
    # Strategy 1: Year-based (2005-2025)
    for year in range(2005, 2026):
        search_queries.append(f'"{molecule}" patent WO{year}')
    
    # Strategy 2: Companies (expanded list)
    companies = [
        "Orion Corporation", "Bayer", "AstraZeneca", "Pfizer", "Novartis",
        "Roche", "Merck", "Bristol-Myers Squibb", "Johnson & Johnson",
        "Eli Lilly", "Sanofi", "GlaxoSmithKline", "AbbVie", "Takeda",
        "Gilead", "Amgen", "Biogen", "Celgene", "Vertex", "Regeneron"
    ]
    for company in companies:
        search_queries.append(f'"{molecule}" "{company}" patent WO')
    
    # Strategy 3: Dev codes
    for dev in dev_codes[:15]:
        search_queries.append(f'"{dev}" patent WO')
        search_queries.append(f'"{dev}" PCT application')
        dev_clean = dev.replace("-", "")
        if dev_clean != dev:
            search_queries.append(f'"{dev_clean}" patent')
    
    # Strategy 4: CAS
    if cas:
        search_queries.append(f'"{cas}" patent WO')
        search_queries.append(f'"{cas}" pharmaceutical patent')
    
    # Strategy 5: Brand
    if brand:
        search_queries.append(f'"{brand}" patent WO')
        search_queries.append(f'"{brand}" pharmaceutical composition')
    
    # Strategy 6: Direct Google Patents searches
    search_queries.append(f'"{molecule}" pharmaceutical composition')
    search_queries.append(f'"{molecule}" treatment method')
    search_queries.append(f'"{molecule}" crystalline form')
    search_queries.append(f'"{molecule}" polymorphic')
    search_queries.append(f'"{molecule}" synthesis process')
    
    debug_log.append(f"[WO Discovery] Built {len(search_queries)} queries")
    
    # Execute searches
    for i, query in enumerate(search_queries):
        try:
            api_key = await api_keys.get_key()
            
            # Use Google Patents engine for better patent discovery
            params = {
                "engine": "google_patents",
                "q": query,
                "api_key": api_key,
                "num": "50"
            }
            
            data = await http_get("https://serpapi.com/search.json", params)
            
            # Extract from organic_results
            for r in data.get("organic_results", []):
                text = f"{r.get('title', '')} {r.get('snippet', '')} {r.get('publication_number', '')} {r.get('patent_id', '')}"
                for wo in extract_wo_numbers(text):
                    all_wo_numbers.add(wo)
                
                # Also check publication_number directly
                pub_num = r.get("publication_number", "")
                if pub_num.startswith("WO"):
                    clean_wo = re.sub(r'[A-Z]\d*$', '', pub_num)
                    all_wo_numbers.add(clean_wo)
            
            await asyncio.sleep(0.3)
            
            # Every 10 queries, do a Google web search too
            if i % 10 == 0:
                api_key = await api_keys.get_key()
                params2 = {
                    "engine": "google",
                    "q": query,
                    "api_key": api_key,
                    "num": "20"
                }
                data2 = await http_get("https://serpapi.com/search.json", params2)
                for r in data2.get("organic_results", []):
                    text = f"{r.get('title', '')} {r.get('snippet', '')} {r.get('link', '')}"
                    for wo in extract_wo_numbers(text):
                        all_wo_numbers.add(wo)
                await asyncio.sleep(0.3)
                
        except Exception:
            continue
    
    wo_list = sorted(list(all_wo_numbers))
    debug_log.append(f"[WO Discovery] Found {len(wo_list)} unique WOs")
    return wo_list

# ============================================================================
# DEEP PATENT EXTRACTION - THE CORE ENGINE
# ============================================================================

async def get_patent_details_direct(patent_id: str, debug_log: list) -> dict:
    """
    Get full patent details using google_patents_details engine
    This is the KEY function that extracts worldwide applications
    """
    
    api_key = await api_keys.get_key()
    
    params = {
        "engine": "google_patents_details",
        "patent_id": patent_id,
        "api_key": api_key
    }
    
    data = await http_get("https://serpapi.com/search.json", params, timeout=60.0)
    await asyncio.sleep(0.5)
    
    return data

async def extract_all_countries_from_wo(wo_number: str, debug_log: list) -> dict:
    """
    Extract ALL country patents from a WO number
    Returns detailed information for each country
    """
    
    result = {
        "wo_number": wo_number,
        "wo_details": {},
        "worldwide_patents": [],
        "br_patents": [],
        "family_members": [],
        "status": "pending",
        "extraction_method": None
    }
    
    try:
        # METHOD 1: Direct google_patents_details with WO number
        debug_log.append(f"[Extract] {wo_number}: Trying direct details...")
        
        patent_data = await get_patent_details_direct(wo_number, debug_log)
        
        if patent_data and not patent_data.get("error"):
            result["extraction_method"] = "direct_details"
            
            # Extract WO details
            result["wo_details"] = {
                "title": patent_data.get("title", ""),
                "abstract": patent_data.get("abstract", "")[:1000] if patent_data.get("abstract") else "",
                "inventors": patent_data.get("inventors", []),
                "assignee": patent_data.get("assignee", ""),
                "filing_date": patent_data.get("filing_date", ""),
                "publication_date": patent_data.get("publication_date", ""),
                "priority_date": patent_data.get("priority_date", ""),
                "claims_count": len(patent_data.get("claims", [])),
                "first_claim": patent_data.get("claims", [{}])[0].get("text", "")[:500] if patent_data.get("claims") else "",
                "classifications": patent_data.get("classifications", [])[:10],
                "legal_status": patent_data.get("legal_status", "")
            }
            
            # Extract worldwide_applications - THIS IS THE KEY!
            worldwide = patent_data.get("worldwide_applications", {})
            
            for year, applications in worldwide.items():
                if isinstance(applications, list):
                    for app in applications:
                        country_patent = {
                            "document_id": app.get("document_id", ""),
                            "country": app.get("document_id", "")[:2] if app.get("document_id") else "",
                            "filing_date": app.get("filing_date", ""),
                            "publication_date": app.get("publication_date", ""),
                            "status": app.get("status", ""),
                            "title": app.get("title", ""),
                            "link": app.get("link", "") or f"https://patents.google.com/patent/{app.get('document_id', '')}",
                            "year": year,
                            "source": "worldwide_applications"
                        }
                        
                        result["worldwide_patents"].append(country_patent)
                        
                        # Separate BR patents
                        if country_patent["document_id"].startswith("BR"):
                            result["br_patents"].append(country_patent)
            
            # Extract family_members
            family = patent_data.get("family_members", [])
            for member in family:
                family_patent = {
                    "document_id": member.get("document_id", "") or member.get("publication_number", ""),
                    "country": (member.get("document_id", "") or member.get("publication_number", ""))[:2],
                    "title": member.get("title", ""),
                    "link": member.get("link", ""),
                    "source": "family_members"
                }
                result["family_members"].append(family_patent)
                
                # Check for BR in family
                if family_patent["document_id"].startswith("BR"):
                    if family_patent not in result["br_patents"]:
                        result["br_patents"].append(family_patent)
            
            # Extract also_published_as
            also_published = patent_data.get("also_published_as", [])
            for pub in also_published:
                if isinstance(pub, str):
                    doc_id = pub
                elif isinstance(pub, dict):
                    doc_id = pub.get("document_id", "") or pub.get("publication_number", "")
                else:
                    continue
                
                if doc_id:
                    pub_patent = {
                        "document_id": doc_id,
                        "country": doc_id[:2],
                        "source": "also_published_as"
                    }
                    
                    # Add to worldwide if not duplicate
                    if not any(p["document_id"] == doc_id for p in result["worldwide_patents"]):
                        result["worldwide_patents"].append(pub_patent)
                    
                    if doc_id.startswith("BR"):
                        if not any(p["document_id"] == doc_id for p in result["br_patents"]):
                            result["br_patents"].append(pub_patent)
            
            # Extract similar_documents for more BR
            similar = patent_data.get("similar_documents", [])
            for sim in similar[:20]:
                doc_id = sim.get("document_id", "") or sim.get("publication_number", "")
                if doc_id.startswith("BR"):
                    sim_patent = {
                        "document_id": doc_id,
                        "country": "BR",
                        "title": sim.get("title", ""),
                        "source": "similar_documents"
                    }
                    if not any(p["document_id"] == doc_id for p in result["br_patents"]):
                        result["br_patents"].append(sim_patent)
            
            # Extract from citations
            citations = patent_data.get("citations", [])
            for cite in citations[:30]:
                doc_id = cite.get("document_id", "") or cite.get("publication_number", "")
                if doc_id.startswith("BR"):
                    cite_patent = {
                        "document_id": doc_id,
                        "country": "BR",
                        "title": cite.get("title", ""),
                        "source": "citations"
                    }
                    if not any(p["document_id"] == doc_id for p in result["br_patents"]):
                        result["br_patents"].append(cite_patent)
            
            result["status"] = "success" if result["worldwide_patents"] or result["br_patents"] else "no_national_patents"
            
        else:
            # METHOD 2: Try via search + serpapi_link chain
            debug_log.append(f"[Extract] {wo_number}: Trying search chain...")
            
            api_key = await api_keys.get_key()
            search_params = {
                "engine": "google_patents",
                "q": wo_number,
                "api_key": api_key
            }
            
            search_data = await http_get("https://serpapi.com/search.json", search_params)
            await asyncio.sleep(0.5)
            
            organic = search_data.get("organic_results", [])
            if organic and organic[0].get("serpapi_link"):
                serpapi_link = organic[0]["serpapi_link"]
                api_key = await api_keys.get_key()
                
                if "api_key=" not in serpapi_link:
                    serpapi_link = f"{serpapi_link}&api_key={api_key}"
                
                detail_data = await http_get(serpapi_link, timeout=60.0)
                await asyncio.sleep(0.5)
                
                if detail_data and not detail_data.get("error"):
                    result["extraction_method"] = "serpapi_link_chain"
                    
                    # Same extraction logic as above
                    worldwide = detail_data.get("worldwide_applications", {})
                    for year, applications in worldwide.items():
                        if isinstance(applications, list):
                            for app in applications:
                                country_patent = {
                                    "document_id": app.get("document_id", ""),
                                    "country": app.get("document_id", "")[:2] if app.get("document_id") else "",
                                    "filing_date": app.get("filing_date", ""),
                                    "status": app.get("status", ""),
                                    "year": year,
                                    "source": "worldwide_applications"
                                }
                                result["worldwide_patents"].append(country_patent)
                                if country_patent["document_id"].startswith("BR"):
                                    result["br_patents"].append(country_patent)
                    
                    result["status"] = "success" if result["br_patents"] else "no_br_patents"
                else:
                    result["status"] = "skipped"
                    result["reason"] = "serpapi_link_failed"
            else:
                result["status"] = "skipped"
                result["reason"] = "no_serpapi_link"
                
    except Exception as e:
        result["status"] = "error"
        result["reason"] = str(e)
    
    debug_log.append(f"[Extract] {wo_number}: {len(result['worldwide_patents'])} worldwide, {len(result['br_patents'])} BR")
    
    return result

# ============================================================================
# GET DETAILED BR PATENT INFO
# ============================================================================

async def get_br_patent_details(br_patent_id: str, debug_log: list) -> dict:
    """Get full details for a specific BR patent"""
    
    details = {
        "number": br_patent_id,
        "title": "",
        "abstract": "",
        "inventors": [],
        "assignee": "",
        "applicant": "",
        "filing_date": "",
        "publication_date": "",
        "grant_date": "",
        "status": "",
        "claims": [],
        "classifications": [],
        "link": f"https://patents.google.com/patent/{br_patent_id}",
        "inpi_link": f"https://busca.inpi.gov.br/pePI/servlet/PatenteServletController?Action=detail&CodPedido={br_patent_id.replace('-', ' ')}"
    }
    
    try:
        # Get details via google_patents_details
        patent_data = await get_patent_details_direct(br_patent_id, debug_log)
        
        if patent_data and not patent_data.get("error"):
            details["title"] = patent_data.get("title", "")
            details["abstract"] = patent_data.get("abstract", "")[:2000] if patent_data.get("abstract") else ""
            details["inventors"] = patent_data.get("inventors", [])
            details["assignee"] = patent_data.get("assignee", "")
            details["applicant"] = patent_data.get("applicant", "") or patent_data.get("assignee", "")
            details["filing_date"] = patent_data.get("filing_date", "")
            details["publication_date"] = patent_data.get("publication_date", "")
            details["grant_date"] = patent_data.get("grant_date", "")
            details["status"] = patent_data.get("legal_status", "")
            details["classifications"] = patent_data.get("classifications", [])[:15]
            
            # Get first 3 claims
            claims = patent_data.get("claims", [])
            for i, claim in enumerate(claims[:3]):
                details["claims"].append({
                    "number": i + 1,
                    "text": claim.get("text", "")[:1000]
                })
        
        await asyncio.sleep(0.3)
        
    except Exception as e:
        debug_log.append(f"[BR Details] Error for {br_patent_id}: {str(e)}")
    
    return details

# ============================================================================
# INPI DIRECT SEARCH
# ============================================================================

async def search_inpi_direct(molecule: str, dev_codes: list, debug_log: list) -> list:
    debug_log.append("[INPI] Starting direct search")
    
    inpi_patents = []
    search_terms = [molecule, molecule.lower()]
    
    # Add dev codes
    for dev in dev_codes[:15]:
        search_terms.append(dev)
        if "-" in dev:
            search_terms.append(dev.replace("-", ""))
    
    # Portuguese variations
    pt_suffixes = [
        ("ide", "ida"),
        ("ine", "ina"),
        ("ib", "ibe"),
        ("ab", "abe"),
        ("mab", "mabe"),
        ("nib", "nibe")
    ]
    
    for en_suffix, pt_suffix in pt_suffixes:
        if molecule.lower().endswith(en_suffix):
            pt_name = molecule[:-len(en_suffix)] + pt_suffix
            search_terms.append(pt_name)
            search_terms.append(pt_name.capitalize())
    
    search_terms = list(set(search_terms))
    debug_log.append(f"[INPI] Searching {len(search_terms)} terms")
    
    for term in search_terms:
        try:
            url = f"https://crawler3-production.up.railway.app/api/data/inpi/patents?medicine={term}"
            data = await http_get(url, timeout=60.0)
            
            if data and data.get("data"):
                for patent in data["data"]:
                    title = patent.get("title", "")
                    if title.startswith("BR"):
                        inpi_patents.append({
                            "number": title.replace(" ", "-"),
                            "title_description": patent.get("applicant", ""),
                            "applicant": patent.get("applicant", ""),
                            "deposit_date": patent.get("depositDate", ""),
                            "full_text_preview": patent.get("fullText", "")[:500] if patent.get("fullText") else "",
                            "source": "inpi_direct",
                            "search_term": term
                        })
            
            await asyncio.sleep(1.5)
        except Exception:
            continue
    
    # Deduplicate
    seen = set()
    unique = []
    for p in inpi_patents:
        norm = re.sub(r'[\s\-/]', '', p["number"]).upper()
        if norm not in seen:
            seen.add(norm)
            unique.append(p)
    
    debug_log.append(f"[INPI] Found {len(unique)} patents")
    return unique

# ============================================================================
# EPO OPS API SEARCH
# ============================================================================

async def get_epo_token() -> str:
    """Get EPO OPS API access token"""
    try:
        import base64
        credentials = base64.b64encode(f"{EPO_KEY}:{EPO_SECRET}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {"grant_type": "client_credentials"}
        
        result = await http_post(
            "https://ops.epo.org/3.2/auth/accesstoken",
            data=data,
            headers=headers
        )
        
        return result.get("access_token", "")
    except Exception:
        return ""

async def search_epo_br_patents(molecule: str, debug_log: list) -> list:
    """Search EPO for BR patents"""
    
    epo_patents = []
    
    try:
        token = await get_epo_token()
        if not token:
            debug_log.append("[EPO] Failed to get token")
            return []
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        # Search for BR patents
        query = f'txt="{molecule}" AND pn=BR*'
        url = f"https://ops.epo.org/3.2/rest-services/published-data/search?q={query}"
        
        data = await http_get(url, headers=headers)
        
        if data:
            # Parse EPO response
            results = data.get("ops:world-patent-data", {}).get("ops:biblio-search", {}).get("ops:search-result", {})
            publications = results.get("ops:publication-reference", [])
            
            if not isinstance(publications, list):
                publications = [publications]
            
            for pub in publications:
                doc_id = pub.get("document-id", {})
                if isinstance(doc_id, list):
                    doc_id = doc_id[0]
                
                country = doc_id.get("country", {}).get("$", "")
                number = doc_id.get("doc-number", {}).get("$", "")
                kind = doc_id.get("kind", {}).get("$", "")
                
                if country == "BR":
                    epo_patents.append({
                        "number": f"{country}{number}{kind}",
                        "source": "epo_ops",
                        "link": f"https://worldwide.espacenet.com/patent/search?q=pn%3D{country}{number}"
                    })
        
        debug_log.append(f"[EPO] Found {len(epo_patents)} BR patents")
        
    except Exception as e:
        debug_log.append(f"[EPO] Error: {str(e)}")
    
    return epo_patents

# ============================================================================
# MAIN SEARCH ENDPOINT
# ============================================================================

@app.post("/search")
async def search_patents(request: dict):
    start_time = datetime.now()
    debug_log = []
    
    molecule = request.get("nome_molecula", "").strip()
    brand = request.get("nome_comercial", "").strip()
    
    if not molecule:
        raise HTTPException(status_code=400, detail="nome_molecula is required")
    
    debug_log.append(f"[START] v7.0 Cortellis Killer - Molecule: {molecule}, Brand: {brand or 'N/A'}")
    
    # Phase 1: PubChem
    pubchem = await get_pubchem_data(molecule, debug_log)
    
    # Phase 2: WO Discovery
    wo_numbers = await discover_wo_numbers(molecule, brand, pubchem["dev_codes"], pubchem["cas"], pubchem["synonyms"], debug_log)
    
    # Phase 3: Deep extraction from each WO
    debug_log.append(f"[Deep Extract] Processing {len(wo_numbers)} WOs with full navigation")
    
    all_worldwide_patents = []
    all_br_patents = []
    wo_results = []
    
    for i, wo in enumerate(wo_numbers):
        debug_log.append(f"[Progress] WO {i+1}/{len(wo_numbers)}: {wo}")
        
        result = await extract_all_countries_from_wo(wo, debug_log)
        wo_results.append(result)
        
        # Collect worldwide patents
        for patent in result["worldwide_patents"]:
            if not any(p["document_id"] == patent["document_id"] for p in all_worldwide_patents):
                patent["from_wo"] = wo
                all_worldwide_patents.append(patent)
        
        # Collect BR patents
        for br in result["br_patents"]:
            if not any(p["document_id"] == br["document_id"] for p in all_br_patents):
                br["from_wo"] = wo
                all_br_patents.append(br)
        
        await asyncio.sleep(1.0)
    
    # Phase 4: Get detailed info for each BR patent
    debug_log.append(f"[BR Details] Getting full details for {len(all_br_patents)} BR patents")
    
    br_with_details = []
    for br in all_br_patents[:50]:  # Limit to 50 for performance
        details = await get_br_patent_details(br["document_id"], debug_log)
        details["from_wo"] = br.get("from_wo", "")
        details["extraction_source"] = br.get("source", "")
        br_with_details.append(details)
        await asyncio.sleep(0.5)
    
    # Phase 5: INPI Direct Search
    inpi_patents = await search_inpi_direct(molecule, pubchem["dev_codes"], debug_log)
    
    # Phase 6: EPO Search
    epo_patents = await search_epo_br_patents(molecule, debug_log)
    
    # Combine all BR patents
    final_br = []
    seen_br = set()
    
    # Add from WO extraction
    for br in br_with_details:
        norm = re.sub(r'[\s\-/]', '', br["number"]).upper()
        if norm not in seen_br:
            seen_br.add(norm)
            br["source"] = "wo_extraction"
            final_br.append(br)
    
    # Add from INPI
    for br in inpi_patents:
        norm = re.sub(r'[\s\-/]', '', br["number"]).upper()
        if norm not in seen_br:
            seen_br.add(norm)
            br["source"] = "inpi_direct"
            final_br.append(br)
    
    # Add from EPO
    for br in epo_patents:
        norm = re.sub(r'[\s\-/]', '', br["number"]).upper()
        if norm not in seen_br:
            seen_br.add(norm)
            br["source"] = "epo_ops"
            final_br.append(br)
    
    # Group worldwide patents by country
    by_country = {}
    for patent in all_worldwide_patents:
        country = patent.get("country", "??")
        if country not in by_country:
            by_country[country] = []
        by_country[country].append(patent)
    
    duration = (datetime.now() - start_time).total_seconds()
    
    # Calculate success metrics
    successful_wos = len([r for r in wo_results if r["status"] == "success"])
    wos_with_data = len([r for r in wo_results if r["worldwide_patents"] or r["br_patents"]])
    
    return {
        "api_version": "7.0 Cortellis Killer",
        "molecule_info": {
            "name": molecule,
            "brand": brand or None,
            "dev_codes": pubchem["dev_codes"],
            "cas": pubchem["cas"],
            "synonyms_count": len(pubchem["synonyms"])
        },
        "wo_discovery": {
            "total_found": len(wo_numbers),
            "wo_numbers": wo_numbers
        },
        "wo_processing": {
            "total_processed": len(wo_results),
            "successful": successful_wos,
            "with_data": wos_with_data,
            "summary": [
                {
                    "wo": r["wo_number"],
                    "worldwide_count": len(r["worldwide_patents"]),
                    "br_count": len(r["br_patents"]),
                    "status": r["status"],
                    "method": r.get("extraction_method", "")
                }
                for r in wo_results
            ]
        },
        "worldwide_patents": {
            "total": len(all_worldwide_patents),
            "by_country": {country: len(patents) for country, patents in by_country.items()},
            "countries_found": list(by_country.keys()),
            "patents": all_worldwide_patents[:200]  # Limit response size
        },
        "br_patents": {
            "total": len(final_br),
            "from_wo_extraction": len([b for b in final_br if b.get("source") == "wo_extraction"]),
            "from_inpi": len([b for b in final_br if b.get("source") == "inpi_direct"]),
            "from_epo": len([b for b in final_br if b.get("source") == "epo_ops"]),
            "patents": final_br
        },
        "inpi_direct_results": {
            "total": len(inpi_patents),
            "patents": inpi_patents
        },
        "epo_results": {
            "total": len(epo_patents),
            "patents": epo_patents
        },
        "comparison": {
            "baseline": "Cortellis",
            "expected_wos": 7,
            "expected_brs": 8,
            "found_wos": len(wo_numbers),
            "found_brs": len(final_br),
            "found_worldwide": len(all_worldwide_patents),
            "wo_rate": f"{round((len(wo_numbers) / 7) * 100)}%" if wo_numbers else "0%",
            "br_rate": f"{round((len(final_br) / 8) * 100)}%" if final_br else "0%",
            "status": "SUPERIOR" if len(final_br) >= 8 else "EXCELLENT" if len(final_br) >= 6 else "GOOD" if len(final_br) >= 4 else "NEEDS_IMPROVEMENT"
        },
        "performance": {
            "duration_seconds": round(duration, 2),
            "duration_minutes": round(duration / 60, 2),
            "wos_processed": len(wo_numbers),
            "timestamp": datetime.now().isoformat()
        },
        "debug_log": debug_log[-100:]  # Last 100 entries
    }

# ============================================================================
# URL-BASED SEARCH (Browser friendly)
# ============================================================================

@app.get("/api/v1/search/{molecule}")
async def search_by_url(molecule: str, brand: Optional[str] = None):
    """Search patents by molecule name via URL"""
    return await search_patents({"nome_molecula": molecule, "nome_comercial": brand})

@app.get("/")
async def root():
    return {
        "name": "Pharmyrus API",
        "version": "7.0 Cortellis Killer",
        "description": "Deep pharmaceutical patent search with worldwide coverage",
        "endpoints": {
            "GET /api/v1/search/{molecule}": "Search by URL (browser)",
            "POST /search": "Search by JSON body",
            "GET /health": "Health check"
        },
        "features": [
            "71+ WO discovery strategies",
            "Deep navigation into worldwide_applications",
            "Full patent details extraction",
            "All country coverage (not just BR)",
            "INPI direct search",
            "EPO OPS API integration"
        ],
        "examples": {
            "browser": "/api/v1/search/darolutamide",
            "with_brand": "/api/v1/search/darolutamide?brand=Nubeqa"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "7.0", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
