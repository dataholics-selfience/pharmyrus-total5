"""
Pharmyrus Patent Search API v6.0
FastAPI application for pharmaceutical patent discovery
Replicates proven n8n workflow logic with additional optimizations
Deploy: Railway
"""

import os
import re
import asyncio
import httpx
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging

# ============================================
# LOGGING CONFIGURATION
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# FASTAPI APP
# ============================================
app = FastAPI(
    title="Pharmyrus Patent Search API",
    description="API para descoberta de patentes farmacêuticas BR a partir de moléculas",
    version="6.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# CONFIGURATION
# ============================================

# SerpAPI keys (rotação automática)
SERPAPI_KEYS = [
    "871b533d956978e967e7621c871d53fb448bc36e90af6389eda2aca3420236e1",
    "bc20bca64032a7ac59abf330bbdeca80aa79cd72bb208059056b10fb6e33e4bc",
    "3f22448f4d43ce8259fa2f7f6385222323a67c4ce4e72fcc774b43d23812889d",
]

# INPI Crawler URL
INPI_CRAWLER_URL = "https://crawler3-production.up.railway.app/api/data/inpi/patents"

# Timeouts
TIMEOUT_SHORT = 30.0
TIMEOUT_MEDIUM = 60.0
TIMEOUT_LONG = 120.0

# ============================================
# REQUEST MODELS
# ============================================

class PatentSearchRequest(BaseModel):
    nome_molecula: str
    nome_comercial: Optional[str] = None

# ============================================
# API KEY ROTATOR
# ============================================

class APIKeyRotator:
    def __init__(self, keys: List[str]):
        self.keys = keys
        self.index = 0
        self.lock = asyncio.Lock()
    
    async def get_key(self) -> str:
        async with self.lock:
            key = self.keys[self.index % len(self.keys)]
            self.index += 1
            return key

serpapi_rotator = APIKeyRotator(SERPAPI_KEYS)

# ============================================
# HTTP CLIENT WITH RETRY
# ============================================

async def http_get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: Optional[Dict] = None,
    timeout: float = TIMEOUT_MEDIUM,
    max_retries: int = 3
) -> Optional[httpx.Response]:
    """HTTP GET with retry logic"""
    for attempt in range(max_retries):
        try:
            response = await client.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:  # Rate limited
                await asyncio.sleep(2 ** attempt)
                continue
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
    return None

# ============================================
# PUBCHEM SERVICE
# ============================================

async def fetch_pubchem_data(molecule: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Fetch synonyms, dev codes, and CAS from PubChem"""
    logger.info(f"[PubChem] Fetching data for: {molecule}")
    
    result = {
        "dev_codes": [],
        "cas": None,
        "synonyms": [],
        "iupac_names": []
    }
    
    try:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{molecule}/synonyms/JSON"
        response = await http_get_with_retry(client, url, timeout=TIMEOUT_SHORT)
        
        if response and response.status_code == 200:
            data = response.json()
            synonyms = data.get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
            
            # Patterns for extraction
            dev_pattern = re.compile(r'^[A-Z]{2,5}[-]?\d{3,7}[A-Z]?$', re.IGNORECASE)
            cas_pattern = re.compile(r'^\d{2,7}-\d{2}-\d$')
            
            for syn in synonyms:
                if not syn:
                    continue
                # Dev codes (e.g., ODM-201, BAY-1841788)
                if dev_pattern.match(syn) and len(result["dev_codes"]) < 25:
                    if syn.upper() not in [d.upper() for d in result["dev_codes"]]:
                        result["dev_codes"].append(syn)
                # CAS number
                if cas_pattern.match(syn) and not result["cas"]:
                    result["cas"] = syn
            
            result["synonyms"] = [s for s in synonyms[:150] if s and len(s) > 2 and len(s) < 100]
            
            logger.info(f"[PubChem] Found {len(result['dev_codes'])} dev codes, CAS: {result['cas']}")
    except Exception as e:
        logger.error(f"[PubChem] Error: {e}")
    
    return result

# ============================================
# WO NUMBER EXTRACTION
# ============================================

def extract_wo_numbers_from_text(text: str) -> Set[str]:
    """Extract WO patent numbers from text using multiple patterns"""
    wo_numbers = set()
    
    if not text:
        return wo_numbers
    
    # Multiple regex patterns to catch different formats
    patterns = [
        r'WO[\s\-]?(\d{4})[\s/\-]?(\d{6})',        # WO 2011 051540, WO2011/051540
        r'WO(\d{4})(\d{6})',                        # WO2011051540
        r'WO[\s\-]?(\d{2})[\s/\-]?(\d{6})',        # WO 11 051540
        r'WO(\d{10,14})',                           # WO2011051540A1
        r'publication[:\s]*(WO\d+)',                # publication: WO2011051540
        r'patent[:\s]*(WO\d+)',                     # patent: WO2011051540
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            groups = match.groups()
            
            if len(groups) == 2:
                year, num = groups[0], groups[1]
                # Handle 2-digit year
                if len(year) == 2:
                    year = f"20{year}" if int(year) < 50 else f"19{year}"
                wo = f"WO{year}{num}"
            elif len(groups) == 1:
                raw = groups[0].upper()
                if raw.startswith("WO"):
                    wo = raw
                else:
                    wo = f"WO{raw}"
            else:
                continue
            
            # Normalize: remove spaces, hyphens, extract base number
            wo = wo.upper().replace(" ", "").replace("-", "").replace("/", "")
            
            # Extract just the core WO number (WO + 4-digit year + 6-digit number)
            wo_match = re.match(r'(WO\d{10})', wo)
            if wo_match:
                wo_normalized = wo_match.group(1)
                
                # Validate year
                try:
                    year_int = int(wo_normalized[2:6])
                    if 1990 <= year_int <= 2025:
                        wo_numbers.add(wo_normalized)
                except ValueError:
                    pass
    
    return wo_numbers

# ============================================
# SERPAPI GOOGLE SEARCH
# ============================================

async def google_search(query: str, client: httpx.AsyncClient, num: int = 15) -> Dict[str, Any]:
    """Execute Google search via SerpAPI"""
    api_key = await serpapi_rotator.get_key()
    
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": num
    }
    
    try:
        response = await http_get_with_retry(client, "https://serpapi.com/search.json", params=params)
        if response:
            return response.json()
    except Exception as e:
        logger.error(f"[Google] Error for '{query[:50]}...': {e}")
    
    return {}

async def google_patents_search(query: str, client: httpx.AsyncClient, num: int = 20) -> Dict[str, Any]:
    """Execute Google Patents search via SerpAPI"""
    api_key = await serpapi_rotator.get_key()
    
    params = {
        "engine": "google_patents",
        "q": query,
        "api_key": api_key,
        "num": num
    }
    
    try:
        response = await http_get_with_retry(client, "https://serpapi.com/search.json", params=params)
        if response:
            return response.json()
    except Exception as e:
        logger.error(f"[GooglePatents] Error for '{query[:50]}...': {e}")
    
    return {}

# ============================================
# PHASE 1: WO DISCOVERY (MULTI-STRATEGY)
# ============================================

async def discover_wo_numbers(
    molecule: str,
    brand: Optional[str],
    dev_codes: List[str],
    cas: Optional[str],
    synonyms: List[str],
    client: httpx.AsyncClient,
    debug_log: List[str]
) -> List[str]:
    """
    Discover WO numbers using multiple search strategies.
    Replicates the proven n8n workflow approach.
    """
    
    all_wo_numbers: Set[str] = set()
    queries = []
    
    # ========================================
    # STRATEGY 1: Year-based searches (MOST EFFECTIVE)
    # ========================================
    years = ["2006", "2008", "2010", "2011", "2012", "2014", "2015", "2016", 
             "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024"]
    for year in years:
        queries.append(f'"{molecule}" patent WO{year}')
    
    # ========================================
    # STRATEGY 2: Company-based searches
    # ========================================
    pharma_companies = [
        "Orion Corporation", "Orion Pharma", "Bayer", "Bayer Pharma AG",
        "AstraZeneca", "Pfizer", "Novartis", "Roche", "Merck",
        "Bristol-Myers Squibb", "Johnson & Johnson", "Eli Lilly",
        "Sanofi", "GlaxoSmithKline", "AbbVie", "Takeda", "Gilead"
    ]
    for company in pharma_companies:
        queries.append(f'"{molecule}" "{company}" patent')
    
    # ========================================
    # STRATEGY 3: Dev code searches
    # ========================================
    for dev in dev_codes[:10]:
        queries.append(f'"{dev}" patent WO')
        queries.append(f'"{dev}" international patent application')
        # Without hyphen
        dev_no_hyphen = dev.replace("-", "")
        if dev_no_hyphen != dev:
            queries.append(f'"{dev_no_hyphen}" patent WO')
    
    # ========================================
    # STRATEGY 4: CAS number search
    # ========================================
    if cas:
        queries.append(f'"{cas}" patent WO')
        queries.append(f'"{cas}" PCT patent')
    
    # ========================================
    # STRATEGY 5: Brand name search
    # ========================================
    if brand:
        queries.append(f'"{brand}" patent WO')
        queries.append(f'"{brand}" pharmaceutical patent international')
    
    # ========================================
    # STRATEGY 6: Direct molecule searches
    # ========================================
    queries.append(f'"{molecule}" WO patent application')
    queries.append(f'"{molecule}" PCT international application')
    queries.append(f'"{molecule}" WIPO patent')
    queries.append(f'site:patents.google.com "{molecule}" WO')
    queries.append(f'site:patentscope.wipo.int "{molecule}"')
    queries.append(f'"{molecule}" pharmaceutical composition patent WO')
    queries.append(f'"{molecule}" treatment method patent WO')
    
    # ========================================
    # STRATEGY 7: Key synonyms (top 5)
    # ========================================
    for syn in synonyms[:5]:
        if len(syn) > 5 and len(syn) < 50:
            queries.append(f'"{syn}" patent WO')
    
    debug_log.append(f"[WO Discovery] Built {len(queries)} search queries")
    logger.info(f"[WO Discovery] Executing {len(queries)} queries...")
    
    # Execute searches with alternating engines
    for i, query in enumerate(queries):
        # Alternate between Google and Google Patents for variety
        if i % 3 == 0:
            result = await google_patents_search(query, client)
        else:
            result = await google_search(query, client)
        
        # Extract WOs from organic results
        organic_results = result.get("organic_results", [])
        for r in organic_results:
            text = " ".join([
                str(r.get("title", "")),
                str(r.get("snippet", "")),
                str(r.get("link", "")),
                str(r.get("publication_number", "")),
                str(r.get("patent_id", ""))
            ])
            
            found_wos = extract_wo_numbers_from_text(text)
            for wo in found_wos:
                if wo not in all_wo_numbers:
                    all_wo_numbers.add(wo)
                    debug_log.append(f"[WO Discovery] Found: {wo} (query {i+1})")
        
        # Rate limiting - be gentle with API
        await asyncio.sleep(0.4)
    
    # Sort by year (newest first) for better relevance
    wo_list = sorted(list(all_wo_numbers), key=lambda x: x[2:6], reverse=True)
    
    debug_log.append(f"[WO Discovery] Total unique WOs: {len(wo_list)}")
    logger.info(f"[WO Discovery] Found {len(wo_list)} unique WO numbers")
    
    return wo_list

# ============================================
# PHASE 2: BR EXTRACTION FROM WO
# ============================================

async def extract_br_from_wo(
    wo_number: str,
    client: httpx.AsyncClient,
    debug_log: List[str]
) -> Dict[str, Any]:
    """
    Extract BR patents from a WO number using Google Patents API chain.
    Follows the exact flow: search → json_endpoint → serpapi_link → worldwide_applications
    """
    
    result = {
        "wo_number": wo_number,
        "br_patents": [],
        "status": "pending",
        "reason": None
    }
    
    try:
        api_key = await serpapi_rotator.get_key()
        
        # ========================================
        # STEP 1: Search WO in Google Patents
        # ========================================
        search_response = await http_get_with_retry(
            client,
            "https://serpapi.com/search.json",
            params={
                "engine": "google_patents",
                "q": wo_number,
                "api_key": api_key,
                "num": 10
            },
            timeout=TIMEOUT_MEDIUM
        )
        
        if not search_response or search_response.status_code != 200:
            result["status"] = "error"
            result["reason"] = "Search failed"
            return result
        
        search_data = search_response.json()
        
        # ========================================
        # STEP 2: Get json_endpoint
        # ========================================
        json_endpoint = search_data.get("search_metadata", {}).get("json_endpoint")
        if not json_endpoint:
            result["status"] = "skip"
            result["reason"] = "No json_endpoint"
            return result
        
        await asyncio.sleep(0.8)
        
        # ========================================
        # STEP 3: Fetch endpoint data
        # ========================================
        endpoint_response = await http_get_with_retry(
            client, json_endpoint, timeout=TIMEOUT_MEDIUM
        )
        
        if not endpoint_response or endpoint_response.status_code != 200:
            result["status"] = "skip"
            result["reason"] = "Endpoint fetch failed"
            return result
        
        endpoint_data = endpoint_response.json()
        
        # ========================================
        # STEP 4: Get serpapi_link from first organic result
        # ========================================
        organic_results = endpoint_data.get("organic_results", [])
        if not organic_results:
            result["status"] = "skip"
            result["reason"] = "No organic results"
            return result
        
        serpapi_link = organic_results[0].get("serpapi_link")
        if not serpapi_link:
            result["status"] = "skip"
            result["reason"] = "No serpapi_link"
            return result
        
        # Add API key if not present
        if "api_key=" not in serpapi_link:
            serpapi_link = f"{serpapi_link}&api_key={api_key}"
        
        await asyncio.sleep(0.8)
        
        # ========================================
        # STEP 5: Fetch patent details (worldwide_applications)
        # ========================================
        details_response = await http_get_with_retry(
            client, serpapi_link, timeout=TIMEOUT_LONG
        )
        
        if not details_response or details_response.status_code != 200:
            result["status"] = "skip"
            result["reason"] = "Details fetch failed"
            return result
        
        patent_data = details_response.json()
        
        # ========================================
        # STEP 6: Extract BR patents from multiple sources
        # ========================================
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
            if isinstance(member, dict):
                pub_num = member.get("publication_number", "")
                if pub_num.startswith("BR"):
                    br_patents.add(pub_num)
        
        # Source C: also_published_as
        also_published = patent_data.get("also_published_as", [])
        for pub in also_published:
            if isinstance(pub, str) and pub.startswith("BR"):
                br_patents.add(pub)
            elif isinstance(pub, dict):
                pub_num = pub.get("publication_number", "")
                if pub_num.startswith("BR"):
                    br_patents.add(pub_num)
        
        # Source D: citations (sometimes has BR refs)
        citations = patent_data.get("citations", [])
        for cite in citations:
            if isinstance(cite, dict):
                pub_num = cite.get("publication_number", "")
                if pub_num.startswith("BR"):
                    br_patents.add(pub_num)
        
        # Source E: similar_documents
        similar = patent_data.get("similar_documents", [])
        for sim in similar:
            if isinstance(sim, dict):
                pub_num = sim.get("publication_number", "")
                if pub_num.startswith("BR"):
                    br_patents.add(pub_num)
        
        result["br_patents"] = list(br_patents)
        result["status"] = "success" if br_patents else "no_br"
        
        if br_patents:
            debug_log.append(f"[BR Extract] {wo_number}: Found {len(br_patents)} BR patents")
        
    except Exception as e:
        result["status"] = "error"
        result["reason"] = str(e)
        logger.error(f"[BR Extract] Error for {wo_number}: {e}")
    
    return result

# ============================================
# PHASE 3: INPI DIRECT SEARCH
# ============================================

async def search_inpi_direct(
    molecule: str,
    dev_codes: List[str],
    client: httpx.AsyncClient,
    debug_log: List[str]
) -> List[Dict[str, Any]]:
    """Search INPI directly for BR patents using the crawler"""
    
    br_patents = []
    seen_numbers = set()
    
    # Build comprehensive search terms
    search_terms = [molecule]
    
    # Add dev codes
    for dev in dev_codes[:12]:
        search_terms.append(dev)
        dev_no_hyphen = dev.replace("-", "")
        if dev_no_hyphen != dev:
            search_terms.append(dev_no_hyphen)
    
    # Portuguese variations
    mol_lower = molecule.lower()
    if mol_lower.endswith("ide"):
        search_terms.append(molecule[:-1] + "a")  # darolutamide → darolutamida
    elif mol_lower.endswith("ine"):
        search_terms.append(molecule[:-1] + "a")  # abiraterone → abiraterona
    elif mol_lower.endswith("ib"):
        search_terms.append(molecule + "e")  # olaparib → olaparibe
    
    debug_log.append(f"[INPI] Searching with {len(search_terms)} terms")
    
    for term in search_terms:
        try:
            url = f"{INPI_CRAWLER_URL}?medicine={term}"
            response = await http_get_with_retry(client, url, timeout=TIMEOUT_LONG)
            
            if response and response.status_code == 200:
                data = response.json()
                patents = data.get("data", [])
                
                for p in patents:
                    title = p.get("title", "")
                    if title.startswith("BR"):
                        patent_num = title.replace(" ", "-")
                        norm_num = patent_num.replace("-", "").replace(" ", "").upper()
                        
                        if norm_num not in seen_numbers:
                            seen_numbers.add(norm_num)
                            br_patents.append({
                                "number": patent_num,
                                "title": p.get("applicant", ""),
                                "filing_date": p.get("depositDate", ""),
                                "full_text": p.get("fullText", "")[:200] if p.get("fullText") else "",
                                "source": "inpi_direct",
                                "link": f"https://busca.inpi.gov.br/pePI/servlet/PatenteServletController?Action=detail&CodPedido={title}"
                            })
                            debug_log.append(f"[INPI] Found: {patent_num}")
            
            await asyncio.sleep(2)  # INPI requires longer delays
            
        except Exception as e:
            debug_log.append(f"[INPI] Error for '{term}': {e}")
    
    debug_log.append(f"[INPI] Total unique BR patents: {len(br_patents)}")
    return br_patents

# ============================================
# MAIN SEARCH ENDPOINT
# ============================================

@app.post("/search")
async def search_patents(request: PatentSearchRequest):
    """
    Main patent search endpoint.
    
    Flow:
    1. Fetch PubChem data (dev codes, CAS, synonyms)
    2. Discover WO numbers via Google/SerpAPI (multiple strategies)
    3. For each WO, extract BR patents via Google Patents chain
    4. Search INPI directly for additional BR patents
    5. Consolidate and return comprehensive results with statistics
    """
    
    start_time = datetime.now()
    debug_log = []
    
    molecule = request.nome_molecula.strip()
    brand = request.nome_comercial.strip() if request.nome_comercial else None
    
    debug_log.append(f"[START] Molecule: {molecule}, Brand: {brand}")
    logger.info(f"Starting patent search for: {molecule}")
    
    async with httpx.AsyncClient() as client:
        # ========================================
        # PHASE 0: PubChem Enrichment
        # ========================================
        pubchem_data = await fetch_pubchem_data(molecule, client)
        dev_codes = pubchem_data["dev_codes"]
        cas = pubchem_data["cas"]
        synonyms = pubchem_data["synonyms"]
        
        debug_log.append(f"[PubChem] Dev codes ({len(dev_codes)}): {dev_codes[:5]}...")
        debug_log.append(f"[PubChem] CAS: {cas}")
        debug_log.append(f"[PubChem] Synonyms: {len(synonyms)}")
        
        # ========================================
        # PHASE 1: WO Discovery
        # ========================================
        wo_numbers = await discover_wo_numbers(
            molecule, brand, dev_codes, cas, synonyms, client, debug_log
        )
        
        # ========================================
        # PHASE 2: BR Extraction from each WO
        # ========================================
        wo_results = []
        all_br_from_wo = set()
        
        debug_log.append(f"[BR Extract] Processing {len(wo_numbers)} WOs...")
        
        for i, wo in enumerate(wo_numbers):
            result = await extract_br_from_wo(wo, client, debug_log)
            wo_results.append(result)
            
            for br in result["br_patents"]:
                all_br_from_wo.add(br)
            
            # Rate limiting
            await asyncio.sleep(1.5)
            
            # Progress logging
            if (i + 1) % 5 == 0:
                debug_log.append(f"[Progress] Processed {i+1}/{len(wo_numbers)} WOs, found {len(all_br_from_wo)} BRs so far")
        
        # ========================================
        # PHASE 3: INPI Direct Search
        # ========================================
        inpi_patents = await search_inpi_direct(molecule, dev_codes, client, debug_log)
        
        # ========================================
        # CONSOLIDATE RESULTS
        # ========================================
        all_br_patents = []
        seen_br = set()
        
        # Add BR from WO extractions
        for br in all_br_from_wo:
            norm = br.replace("-", "").replace(" ", "").upper()
            if norm not in seen_br:
                seen_br.add(norm)
                all_br_patents.append({
                    "number": br,
                    "source": "wo_extraction",
                    "link": f"https://patents.google.com/patent/{br}"
                })
        
        # Add INPI patents
        for p in inpi_patents:
            norm = p["number"].replace("-", "").replace(" ", "").upper()
            if norm not in seen_br:
                seen_br.add(norm)
                all_br_patents.append({
                    "number": p["number"],
                    "source": "inpi_direct",
                    "link": p["link"],
                    "title": p.get("title"),
                    "filing_date": p.get("filing_date")
                })
    
    # ========================================
    # CALCULATE STATISTICS
    # ========================================
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    successful = [r for r in wo_results if r["status"] == "success"]
    no_br = [r for r in wo_results if r["status"] == "no_br"]
    skipped = [r for r in wo_results if r["status"] in ["skip", "error"]]
    
    # Cortellis baseline for darolutamide comparison
    expected_wos = 7
    expected_brs = 8
    
    # ========================================
    # BUILD RESPONSE
    # ========================================
    response = {
        "molecule_info": {
            "name": molecule,
            "brand": brand,
            "dev_codes": dev_codes,
            "cas": cas,
            "synonyms_count": len(synonyms)
        },
        "search_strategy": {
            "version": "v6.0 FastAPI Production",
            "method": "Multi-source WO Discovery → Google Patents BR Extraction → INPI Direct",
            "layers": [
                "Layer 1: PubChem enrichment (dev codes, CAS, synonyms)",
                "Layer 2: Google Search WO discovery (year-based queries)",
                "Layer 3: Google Patents WO discovery",
                "Layer 4: Company-based patent searches",
                "Layer 5: Dev code patent searches",
                "Layer 6: Google Patents worldwide_applications BR extraction",
                "Layer 7: INPI Direct crawler search"
            ]
        },
        "wo_discovery": {
            "total_found": len(wo_numbers),
            "wo_numbers": wo_numbers
        },
        "wo_processing": {
            "total_processed": len(wo_results),
            "successful": len(successful),
            "with_br_patents": len(successful),
            "no_br_patents": len(no_br),
            "skipped_or_error": len(skipped),
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
            "from_inpi_direct": len([p for p in all_br_patents if p["source"] == "inpi_direct"]),
            "patents": all_br_patents
        },
        "inpi_results": {
            "total": len(inpi_patents),
            "patents": inpi_patents
        },
        "comparison": {
            "baseline": "Cortellis",
            "expected_wos": expected_wos,
            "expected_brs": expected_brs,
            "wo_found": len(wo_numbers),
            "br_found": len(all_br_patents),
            "wo_coverage": f"{len(wo_numbers)}/{expected_wos}",
            "br_coverage": f"{len(all_br_patents)}/{expected_brs}",
            "wo_rate": f"{min(len(wo_numbers) / max(expected_wos, 1) * 100, 999):.0f}%",
            "br_rate": f"{min(len(all_br_patents) / max(expected_brs, 1) * 100, 999):.0f}%",
            "status": (
                "Excellent" if len(all_br_patents) >= expected_brs else
                "Good" if len(all_br_patents) >= expected_brs * 0.75 else
                "Acceptable" if len(all_br_patents) >= expected_brs * 0.5 else
                "Needs improvement"
            )
        },
        "performance": {
            "duration_seconds": round(duration, 2),
            "api_calls_estimate": len(wo_numbers) * 3 + 60,
            "timestamp": end_time.isoformat()
        },
        "debug_log": debug_log
    }
    
    logger.info(f"Search completed: {len(wo_numbers)} WOs, {len(all_br_patents)} BRs in {duration:.1f}s")
    
    return response

# ============================================
# HEALTH & INFO ENDPOINTS
# ============================================

@app.get("/")
async def root():
    return {
        "service": "Pharmyrus Patent Search API",
        "version": "6.0.0",
        "description": "API para descoberta de patentes farmacêuticas BR a partir de moléculas",
        "endpoints": {
            "POST /search": {
                "description": "Busca patentes por nome da molécula",
                "body": {
                    "nome_molecula": "string (obrigatório)",
                    "nome_comercial": "string (opcional)"
                },
                "example": {
                    "nome_molecula": "darolutamide",
                    "nome_comercial": "Nubeqa"
                }
            },
            "GET /health": "Health check"
        }
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "6.0.0"
    }

# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
