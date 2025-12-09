"""
Pharmyrus API v6.0 - Brazilian Pharmaceutical Patent Search
FastAPI Production Application for Railway Deployment

Replicates proven n8n workflow logic:
- Multi-source WO Discovery (7 strategies)
- Google Patents Chain (json_endpoint → serpapi_link → worldwide_applications)
- INPI Direct Search
"""

import asyncio
import httpx
import re
import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ============================================================================
# FASTAPI APP INITIALIZATION
# ============================================================================

app = FastAPI(
    title="Pharmyrus API",
    description="Brazilian Pharmaceutical Patent Search - v6.0 Production",
    version="6.0.0"
)

# ============================================================================
# API KEY ROTATION
# ============================================================================

class APIKeyRotator:
    def __init__(self):
        self.keys = [
            "3f22448f4d43ce8259fa2f7f6385222323a67c4ce4e72fcc774b43d23812889d",
            "bc20bca64032a7ac59abf330bbdeca80aa79cd72bb208059056b10fb6e33e4bc",
            "aad6d736889f91f9e7fe5a094336589404d04eda73fee9b158e328c2bd5a4d7e"
        ]
        self.current_index = 0
        self.lock = asyncio.Lock()
    
    async def get_key(self) -> str:
        async with self.lock:
            key = self.keys[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.keys)
            return key

api_keys = APIKeyRotator()

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class SearchRequest(BaseModel):
    nome_molecula: str
    nome_comercial: Optional[str] = None

class SearchResponse(BaseModel):
    molecule_info: dict
    search_strategy: dict
    wo_discovery: dict
    wo_processing: dict
    br_patents: dict
    inpi_results: dict
    comparison: dict
    performance: dict
    debug_log: list

# ============================================================================
# HTTP CLIENT WITH RETRY
# ============================================================================

async def http_get_with_retry(url: str, params: dict = None, max_retries: int = 3, timeout: float = 30.0) -> dict:
    """HTTP GET with retry logic and rate limit handling"""
    
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)
                
                if response.status_code == 429:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                
                response.raise_for_status()
                return response.json()
                
        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            return {}
        except Exception:
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)
                continue
            return {}
    
    return {}

# ============================================================================
# PUBCHEM SERVICE
# ============================================================================

async def get_pubchem_data(molecule: str, debug_log: list) -> dict:
    """Fetch synonyms, dev codes, and CAS from PubChem"""
    
    debug_log.append(f"[PubChem] Fetching data for: {molecule}")
    
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{molecule}/synonyms/JSON"
    
    data = await http_get_with_retry(url)
    
    dev_codes = []
    cas = None
    synonyms = []
    
    try:
        syns = data.get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
        
        dev_pattern = re.compile(r'^[A-Z]{2,5}[-]?\d{3,7}[A-Z]?$', re.IGNORECASE)
        cas_pattern = re.compile(r'^\d{2,7}-\d{2}-\d$')
        
        for s in syns[:150]:
            if dev_pattern.match(s) and len(dev_codes) < 25:
                dev_codes.append(s)
            if cas_pattern.match(s) and not cas:
                cas = s
            if len(s) > 3 and len(s) < 50:
                synonyms.append(s)
        
        debug_log.append(f"[PubChem] Found {len(dev_codes)} dev codes, CAS: {cas or 'None'}")
        
    except Exception as e:
        debug_log.append(f"[PubChem] Error: {str(e)}")
    
    return {
        "dev_codes": dev_codes,
        "cas": cas,
        "synonyms": synonyms[:50]
    }

# ============================================================================
# WO NUMBER EXTRACTION
# ============================================================================

def extract_wo_numbers(text: str) -> list:
    """Extract WO numbers from text using multiple patterns"""
    
    patterns = [
        r'WO[\s-]?(\d{4})[\s/]?(\d{6})',
        r'WO(\d{4})(\d{6})[A-Z]?\d?',
        r'WO\s?(\d{4})/(\d{6})',
        r'WO(\d{4})[\s-](\d{6})',
        r'PCT/[A-Z]{2}(\d{4})/(\d{6})',
        r'WO[\s]?(\d{2})[\s/]?(\d{5,6})'
    ]
    
    wo_numbers = set()
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            if len(m) == 2:
                year = m[0]
                number = m[1]
                
                if len(year) == 2:
                    year = "20" + year if int(year) < 50 else "19" + year
                
                number = number.zfill(6)
                wo = f"WO{year}{number}"
                wo_numbers.add(wo)
    
    return list(wo_numbers)

# ============================================================================
# WO DISCOVERY - MULTI-SOURCE SEARCH
# ============================================================================

async def discover_wo_numbers(molecule: str, brand: str, dev_codes: list, cas: str, synonyms: list, debug_log: list) -> list:
    """Discover WO numbers using 7 different search strategies"""
    
    debug_log.append("[WO Discovery] Starting multi-source search")
    
    all_wo_numbers = set()
    search_queries = []
    
    # Strategy 1: Year-based searches (2006-2024)
    for year in range(2006, 2025):
        search_queries.append({
            "query": f'"{molecule}" patent WO{year}',
            "strategy": "year_based"
        })
    
    # Strategy 2: Company-based searches
    companies = [
        "Orion Corporation", "Bayer", "AstraZeneca", "Pfizer", "Novartis",
        "Roche", "Merck", "Bristol-Myers Squibb", "Johnson & Johnson",
        "Eli Lilly", "Sanofi", "GlaxoSmithKline", "AbbVie", "Takeda",
        "Gilead", "Amgen", "Biogen"
    ]
    for company in companies:
        search_queries.append({
            "query": f'"{molecule}" "{company}" patent',
            "strategy": "company_based"
        })
    
    # Strategy 3: Dev code searches
    for dev in dev_codes[:10]:
        search_queries.append({
            "query": f'"{dev}" patent WO',
            "strategy": "dev_code"
        })
        search_queries.append({
            "query": f'"{dev}" international patent application',
            "strategy": "dev_code"
        })
        dev_no_hyphen = dev.replace("-", "")
        if dev_no_hyphen != dev:
            search_queries.append({
                "query": f'"{dev_no_hyphen}" patent WO',
                "strategy": "dev_code_variant"
            })
    
    # Strategy 4: CAS number
    if cas:
        search_queries.append({
            "query": f'"{cas}" patent WO',
            "strategy": "cas_number"
        })
        search_queries.append({
            "query": f'"{cas}" PCT patent',
            "strategy": "cas_number"
        })
    
    # Strategy 5: Brand name
    if brand:
        search_queries.append({
            "query": f'"{brand}" patent WO',
            "strategy": "brand_name"
        })
        search_queries.append({
            "query": f'"{brand}" pharmaceutical patent international',
            "strategy": "brand_name"
        })
    
    # Strategy 6: Direct molecule searches
    direct_queries = [
        f'"{molecule}" WO patent application',
        f'"{molecule}" PCT international application',
        f'site:patents.google.com "{molecule}" WO',
        f'site:patentscope.wipo.int "{molecule}"',
        f'"{molecule}" pharmaceutical composition patent WO',
        f'"{molecule}" treatment cancer patent WO'
    ]
    for q in direct_queries:
        search_queries.append({
            "query": q,
            "strategy": "direct_molecule"
        })
    
    # Strategy 7: Key synonyms
    for syn in synonyms[:5]:
        if syn.lower() != molecule.lower():
            search_queries.append({
                "query": f'"{syn}" patent WO',
                "strategy": "synonym"
            })
    
    debug_log.append(f"[WO Discovery] Built {len(search_queries)} search queries")
    
    # Execute searches with rate limiting
    for i, sq in enumerate(search_queries):
        try:
            api_key = await api_keys.get_key()
            
            # Alternate between Google Search and Google Patents
            if i % 3 == 0:
                params = {
                    "engine": "google_patents",
                    "q": sq["query"],
                    "api_key": api_key,
                    "num": "20"
                }
            else:
                params = {
                    "engine": "google",
                    "q": sq["query"],
                    "api_key": api_key,
                    "num": "10"
                }
            
            data = await http_get_with_retry("https://serpapi.com/search.json", params)
            
            # Extract from organic results
            results = data.get("organic_results", [])
            for r in results:
                text = f"{r.get('title', '')} {r.get('snippet', '')} {r.get('link', '')}"
                wo_found = extract_wo_numbers(text)
                for wo in wo_found:
                    all_wo_numbers.add(wo)
            
            # Extract from patent results (if Google Patents)
            patent_results = data.get("patents", [])
            for p in patent_results:
                pub_num = p.get("publication_number", "")
                if pub_num.startswith("WO"):
                    all_wo_numbers.add(pub_num[:14])
            
            # Rate limiting
            await asyncio.sleep(0.4)
            
        except Exception as e:
            debug_log.append(f"[WO Discovery] Error in query {i}: {str(e)}")
            continue
    
    wo_list = sorted(list(all_wo_numbers))
    debug_log.append(f"[WO Discovery] Total unique WOs found: {len(wo_list)}")
    
    return wo_list

# ============================================================================
# BR EXTRACTION FROM WO (GOOGLE PATENTS CHAIN)
# ============================================================================

async def extract_br_from_wo(wo_number: str, debug_log: list) -> dict:
    """Extract BR patents from WO using Google Patents chain"""
    
    result = {
        "wo_number": wo_number,
        "br_patents": [],
        "status": "pending",
        "reason": None
    }
    
    try:
        # Step 1: Search WO in Google Patents
        api_key = await api_keys.get_key()
        params = {
            "engine": "google_patents",
            "q": wo_number,
            "api_key": api_key
        }
        
        search_data = await http_get_with_retry("https://serpapi.com/search.json", params)
        await asyncio.sleep(0.8)
        
        # Step 2: Get json_endpoint
        json_endpoint = search_data.get("search_metadata", {}).get("json_endpoint")
        
        if not json_endpoint:
            result["status"] = "skipped"
            result["reason"] = "no_json_endpoint"
            return result
        
        # Step 3: Fetch endpoint data
        endpoint_data = await http_get_with_retry(json_endpoint)
        await asyncio.sleep(0.8)
        
        # Step 4: Get serpapi_link
        organic_results = endpoint_data.get("organic_results", [])
        if not organic_results:
            result["status"] = "skipped"
            result["reason"] = "no_organic_results"
            return result
        
        serpapi_link = organic_results[0].get("serpapi_link")
        if not serpapi_link:
            result["status"] = "skipped"
            result["reason"] = "no_serpapi_link"
            return result
        
        # Add API key to serpapi_link
        api_key = await api_keys.get_key()
        if "api_key=" not in serpapi_link:
            serpapi_link = f"{serpapi_link}&api_key={api_key}"
        
        # Step 5: Fetch patent details
        patent_data = await http_get_with_retry(serpapi_link)
        await asyncio.sleep(0.8)
        
        # Step 6: Extract BR patents from multiple sources
        br_patents = set()
        
        # Source A: worldwide_applications (PRIMARY)
        worldwide = patent_data.get("worldwide_applications", {})
        for year, apps in worldwide.items():
            if isinstance(apps, list):
                for app in apps:
                    doc_id = app.get("document_id", "")
                    if doc_id.startswith("BR"):
                        br_patents.add(doc_id)
        
        # Source B: family_members
        family = patent_data.get("family_members", [])
        for member in family:
            doc_id = member.get("document_id", "") or member.get("publication_number", "")
            if doc_id.startswith("BR"):
                br_patents.add(doc_id)
        
        # Source C: also_published_as
        also_published = patent_data.get("also_published_as", [])
        for pub in also_published:
            if isinstance(pub, str) and pub.startswith("BR"):
                br_patents.add(pub)
            elif isinstance(pub, dict):
                doc_id = pub.get("document_id", "")
                if doc_id.startswith("BR"):
                    br_patents.add(doc_id)
        
        # Source D: citations
        citations = patent_data.get("citations", [])
        for cite in citations[:50]:
            doc_id = cite.get("document_id", "") or cite.get("publication_number", "")
            if doc_id.startswith("BR"):
                br_patents.add(doc_id)
        
        # Source E: similar_documents
        similar = patent_data.get("similar_documents", [])
        for sim in similar[:30]:
            doc_id = sim.get("document_id", "") or sim.get("publication_number", "")
            if doc_id.startswith("BR"):
                br_patents.add(doc_id)
        
        result["br_patents"] = list(br_patents)
        result["status"] = "success" if br_patents else "no_br_patents"
        
    except Exception as e:
        result["status"] = "error"
        result["reason"] = str(e)
    
    return result

# ============================================================================
# INPI DIRECT SEARCH
# ============================================================================

async def search_inpi_direct(molecule: str, dev_codes: list, debug_log: list) -> list:
    """Search INPI directly for BR patents"""
    
    debug_log.append("[INPI] Starting direct search")
    
    inpi_patents = []
    search_terms = [molecule]
    
    # Add dev codes
    for dev in dev_codes[:12]:
        search_terms.append(dev)
        dev_no_hyphen = dev.replace("-", "")
        if dev_no_hyphen != dev:
            search_terms.append(dev_no_hyphen)
    
    # Add Portuguese variations
    pt_variations = {
        "ide": "ida",
        "ine": "ina",
        "ib": "ibe",
        "ab": "abe"
    }
    
    for suffix_en, suffix_pt in pt_variations.items():
        if molecule.lower().endswith(suffix_en):
            pt_name = molecule[:-len(suffix_en)] + suffix_pt
            search_terms.append(pt_name)
    
    search_terms = list(set(search_terms))
    debug_log.append(f"[INPI] Searching {len(search_terms)} terms")
    
    for term in search_terms:
        try:
            url = f"https://crawler3-production.up.railway.app/api/data/inpi/patents?medicine={term}"
            data = await http_get_with_retry(url, timeout=60.0)
            
            if data and data.get("data"):
                for patent in data["data"]:
                    title = patent.get("title", "")
                    if title.startswith("BR"):
                        inpi_patents.append({
                            "number": title.replace(" ", "-"),
                            "applicant": patent.get("applicant", ""),
                            "deposit_date": patent.get("depositDate", ""),
                            "source": "inpi_direct",
                            "search_term": term
                        })
            
            await asyncio.sleep(2.0)  # INPI needs longer delays
            
        except Exception as e:
            debug_log.append(f"[INPI] Error searching '{term}': {str(e)}")
            continue
    
    # Deduplicate
    seen = set()
    unique_patents = []
    for p in inpi_patents:
        normalized = re.sub(r'[\s\-/]', '', p["number"]).upper()
        if normalized not in seen:
            seen.add(normalized)
            unique_patents.append(p)
    
    debug_log.append(f"[INPI] Found {len(unique_patents)} unique patents")
    
    return unique_patents

# ============================================================================
# MAIN SEARCH ENDPOINT
# ============================================================================

@app.post("/search", response_model=SearchResponse)
async def search_patents(request: SearchRequest):
    """Main patent search endpoint"""
    
    start_time = datetime.now()
    debug_log = []
    
    molecule = request.nome_molecula.strip()
    brand = (request.nome_comercial or "").strip()
    
    if not molecule:
        raise HTTPException(status_code=400, detail="nome_molecula is required")
    
    debug_log.append(f"[START] Molecule: {molecule}, Brand: {brand or 'N/A'}")
    
    # Phase 1: Get PubChem data
    pubchem_data = await get_pubchem_data(molecule, debug_log)
    dev_codes = pubchem_data["dev_codes"]
    cas = pubchem_data["cas"]
    synonyms = pubchem_data["synonyms"]
    
    # Phase 2: Discover WO numbers
    wo_numbers = await discover_wo_numbers(molecule, brand, dev_codes, cas, synonyms, debug_log)
    
    # Phase 3: Extract BR patents from each WO
    debug_log.append(f"[BR Extraction] Processing {len(wo_numbers)} WO numbers")
    
    wo_results = []
    all_br_from_wo = []
    
    for wo in wo_numbers:
        result = await extract_br_from_wo(wo, debug_log)
        wo_results.append(result)
        
        if result["br_patents"]:
            for br in result["br_patents"]:
                all_br_from_wo.append({
                    "number": br,
                    "source": "wo_extraction",
                    "from_wo": wo,
                    "link": f"https://patents.google.com/patent/{br}"
                })
        
        await asyncio.sleep(1.5)  # Rate limit between WOs
    
    # Phase 4: INPI Direct Search
    inpi_patents = await search_inpi_direct(molecule, dev_codes, debug_log)
    
    # Combine and deduplicate BR patents
    all_br_patents = []
    seen_br = set()
    
    for br in all_br_from_wo:
        normalized = re.sub(r'[\s\-/]', '', br["number"]).upper()
        if normalized not in seen_br:
            seen_br.add(normalized)
            all_br_patents.append(br)
    
    for br in inpi_patents:
        normalized = re.sub(r'[\s\-/]', '', br["number"]).upper()
        if normalized not in seen_br:
            seen_br.add(normalized)
            all_br_patents.append(br)
    
    # Calculate statistics
    successful_wos = len([r for r in wo_results if r["status"] == "success"])
    wos_with_br = len([r for r in wo_results if r["br_patents"]])
    no_br_wos = len([r for r in wo_results if r["status"] == "no_br_patents"])
    skipped_wos = len([r for r in wo_results if r["status"] in ["skipped", "error"]])
    
    # Calculate duration
    duration = (datetime.now() - start_time).total_seconds()
    
    # Build response
    response = {
        "molecule_info": {
            "name": molecule,
            "brand": brand or None,
            "dev_codes": dev_codes,
            "cas": cas,
            "synonyms_count": len(synonyms)
        },
        "search_strategy": {
            "version": "v6.0 FastAPI Production",
            "method": "Multi-source WO Discovery → Google Patents BR Extraction → INPI Direct",
            "layers": [
                "Layer 1: Year-based WO search (2006-2024)",
                "Layer 2: Company-based search (17 pharma companies)",
                "Layer 3: Dev code searches with variants",
                "Layer 4: CAS number search",
                "Layer 5: Brand name search",
                "Layer 6: Direct molecule queries",
                "Layer 7: Synonym searches"
            ]
        },
        "wo_discovery": {
            "total_found": len(wo_numbers),
            "wo_numbers": wo_numbers
        },
        "wo_processing": {
            "total_processed": len(wo_results),
            "successful": successful_wos,
            "with_br_patents": wos_with_br,
            "no_br_patents": no_br_wos,
            "skipped_or_error": skipped_wos,
            "details": [
                {
                    "wo_number": r["wo_number"],
                    "br_count": len(r["br_patents"]),
                    "br_patents": r["br_patents"],
                    "status": r["status"],
                    "reason": r.get("reason")
                }
                for r in wo_results
            ]
        },
        "br_patents": {
            "total": len(all_br_patents),
            "from_wo_extraction": len(all_br_from_wo),
            "from_inpi_direct": len([p for p in all_br_patents if p.get("source") == "inpi_direct"]),
            "patents": all_br_patents
        },
        "inpi_results": {
            "total": len(inpi_patents),
            "patents": inpi_patents
        },
        "comparison": {
            "baseline": "Cortellis",
            "expected_wos": 7,
            "expected_brs": 8,
            "wo_found": len(wo_numbers),
            "br_found": len(all_br_patents),
            "wo_coverage": f"{len(wo_numbers)}/7",
            "br_coverage": f"{len(all_br_patents)}/8",
            "wo_rate": f"{round((len(wo_numbers) / 7) * 100)}%" if wo_numbers else "0%",
            "br_rate": f"{round((min(len(all_br_patents), 8) / 8) * 100)}%",
            "status": "Excellent" if len(all_br_patents) >= 6 else "Good" if len(all_br_patents) >= 4 else "Low"
        },
        "performance": {
            "duration_seconds": round(duration, 2),
            "api_calls_estimate": len(wo_numbers) * 4 + 60,
            "timestamp": datetime.now().isoformat()
        },
        "debug_log": debug_log
    }
    
    return response

# ============================================================================
# HEALTH CHECK AND INFO ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """API information"""
    return {
        "name": "Pharmyrus API",
        "version": "6.0.0",
        "description": "Brazilian Pharmaceutical Patent Search",
        "endpoints": {
            "POST /search": "Main patent search",
            "GET /health": "Health check"
        },
        "example_request": {
            "nome_molecula": "darolutamide",
            "nome_comercial": "Nubeqa"
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "6.0.0",
        "timestamp": datetime.now().isoformat()
    }

# ============================================================================
# RUN SERVER (for local development)
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
