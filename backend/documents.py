"""Document quality, optional OCR, validation and masked name match."""
from __future__ import annotations
import difflib, re, uuid
from io import BytesIO
from .config import PROFILES
from .store import get_session, update_session, log_event, save_document, create_handoff
MAX_SIZE=10*1024*1024
try:
    import cv2
    import numpy as np
except ImportError:
    cv2=None; np=None

def mask_name(name: str) -> str:
    parts=[p for p in re.split(r"\s+",name.strip()) if p]
    return " ".join(p[0].upper()+"***" for p in parts) if parts else ""

def image_quality(data: bytes) -> tuple[float,list[str],dict]:
    if not cv2: return 0.0,["OpenCV is not installed; image quality check is unavailable."],{}
    arr=np.frombuffer(data,dtype=np.uint8); image=cv2.imdecode(arr,cv2.IMREAD_COLOR)
    if image is None: raise ValueError("Could not open this image. Try JPEG or PNG.")
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY); sharp=float(cv2.Laplacian(gray,cv2.CV_64F).var()); brightness=float(gray.mean()); glare=float(np.mean(gray>=250)); height,width=gray.shape
    issues=[]
    if min(width,height)<700: issues.append("Resolution is low. Move closer and keep the full document in frame.")
    if sharp<70: issues.append("Photo is blurry. Hold the camera steady and retake in good light.")
    if brightness<55: issues.append("Photo is too dark. Move to brighter, even light.")
    if brightness>220 or glare>0.14: issues.append("Photo has glare or is overexposed. Tilt the document away from reflections.")
    # A small border check catches severe cropping; it cannot prove all corners are present.
    edge=cv2.Canny(gray,60,150); margin=max(2,min(height,width)//50); border=max(edge[:margin,:].mean(),edge[-margin:,:].mean(),edge[:,:margin].mean(),edge[:,-margin:].mean())
    if border<1.2: issues.append("Document edges may be cropped. Include all four corners.")
    quality=max(0,min(100,0.55*min(100,sharp/2)+0.3*max(0,100-abs(brightness-145)) + 0.15*max(0,100-glare*300)))
    return quality,issues,{"width":width,"height":height,"sharpness":round(sharp,1),"brightness":round(brightness,1),"glare_fraction":round(glare,3)}

def read_ocr(data: bytes, supplied_text: str="") -> tuple[str,str]:
    # A supplied value is explicitly a demo OCR fallback. Never treat it as verified identity.
    if supplied_text.strip(): return supplied_text.strip()[:1000],"manual_demo_fallback"
    try:
        import pytesseract
        import cv2, numpy as np
        image=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
        if image is None: return "","unavailable"
        text=pytesseract.image_to_string(image,config="--psm 6")
        return text[:2000],"tesseract"
    except Exception:
        return "","unavailable"

def extract_pdf_text(data: bytes) -> tuple[str,int]:
    from pypdf import PdfReader
    try: reader=PdfReader(BytesIO(data),strict=False)
    except Exception as exc: raise ValueError("Could not read this PDF. Export it again and retry.") from exc
    if reader.is_encrypted: raise ValueError("Password-protected PDFs are not supported in this demo.")
    if len(reader.pages)>10: raise ValueError("PDFs must contain 10 pages or fewer.")
    return "\n".join((page.extract_text() or "") for page in reader.pages)[:20000],len(reader.pages)

_VERHOEFF_D=((0,1,2,3,4,5,6,7,8,9),(1,2,3,4,0,6,7,8,9,5),(2,3,4,0,1,7,8,9,5,6),(3,4,0,1,2,8,9,5,6,7),(4,0,1,2,3,9,5,6,7,8),(5,9,8,7,6,0,4,3,2,1),(6,5,9,8,7,1,0,4,3,2),(7,6,5,9,8,2,1,0,4,3),(8,7,6,5,9,3,2,1,0,4),(9,8,7,6,5,4,3,2,1,0))
_VERHOEFF_P=((0,1,2,3,4,5,6,7,8,9),(1,5,7,6,2,8,3,0,9,4),(5,8,0,3,7,9,6,1,4,2),(8,9,1,6,0,4,3,5,2,7),(9,4,5,3,1,2,6,8,7,0),(4,2,8,6,5,7,3,9,0,1),(2,7,9,3,8,0,6,4,1,5),(7,0,4,6,9,1,3,2,5,8))
def valid_aadhaar_checksum(value: str) -> bool:
    digits=re.sub(r"\s+","",value)
    if not re.fullmatch(r"[2-9]\d{11}",digits): return False
    check=0
    for i,digit in enumerate(reversed(digits)): check=_VERHOEFF_D[check][_VERHOEFF_P[i%8][int(digit)]]
    return check==0

def verify_upload(session_id: str, filename: str, content_type: str, data: bytes, doc_type: str, supplied_text: str="") -> dict:
    session=get_session(session_id)
    if not session: raise ValueError("Session not found. Start a new demo journey.")
    if session["state"] not in ("KYC","KYC_RETRY"): raise ValueError("Complete the eligibility step before uploading a demo document.")
    if len(data)>MAX_SIZE: raise ValueError("Maximum upload size is 10 MB.")
    mime=content_type.split(";",1)[0].strip().lower(); filename=filename.lower()
    image_mimes={"image/jpeg":(".jpg",".jpeg"),"image/png":(".png",),"image/webp":(".webp",)}
    is_pdf=mime=="application/pdf" and filename.endswith(".pdf")
    is_image=mime in image_mimes and filename.endswith(image_mimes[mime])
    if not (is_pdf or is_image): raise ValueError("Upload a JPEG, PNG, WebP or text-based PDF dummy document.")
    if is_pdf:
        extracted,pages=extract_pdf_text(data)
        if not extracted.strip() and supplied_text.strip(): extracted=supplied_text[:1000]; ocr_method="manual_demo_fallback"
        else: ocr_method="pdf_text_layer"
        quality=None; issues=[] if extracted.strip() else ["No selectable text found. Scanned PDFs need OCR; use an image or manual demo text."]
        metrics={"format":"pdf","pages":pages}
    else:
        quality,issues,metrics=image_quality(data); extracted,ocr_method=read_ocr(data,supplied_text)
    kind=doc_type.upper().strip()
    if kind not in ("PAN","AADHAAR","OTHER DEMO ID"): raise ValueError("Choose PAN, Aadhaar demo ID or other demo ID.")
    profile=PROFILES[session["profile_id"]]; target=profile["name"]
    found=re.search(r"\bname\s*[:\-]?\s*([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}?)(?=\s+(?:DOB|DATE|PAN|AADHAAR|UID|ID)\b|\s*$)",extracted,re.I)
    if not found: found=re.search(r"(?:^|\n)\s*([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,2})\s*(?:\n|$)",extracted)
    extracted_name=found.group(1).strip() if found else ""
    if not extracted_name: issues.append("Could not read a name. Retake the dummy document or type the visible demo name into the OCR fallback field.")
    score=difflib.SequenceMatcher(None,re.sub(r"[^a-z]","",target.lower()),re.sub(r"[^a-z]","",extracted_name.lower())).ratio() if extracted_name else 0
    if extracted_name and score<0.78: issues.append("Name does not match the selected sample profile. Check the demo profile or retake the document.")
    id_ok=True; masked_id=""
    if kind=="PAN":
        pan=re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b",extracted.upper()); id_ok=bool(pan)
        if not pan: issues.append("The synthetic PAN format was not recognized. Use a dummy value such as ABCDE1234F; never enter a real PAN.")
        else: masked_id=pan.group(0)[:2]+"*****"+pan.group(0)[-1]
    elif kind=="AADHAAR":
        aadhaar=re.search(r"(?<!\d)([2-9](?:\s*\d){11})(?!\d)",extracted)
        id_ok=bool(aadhaar and valid_aadhaar_checksum(aadhaar.group(1)))
        if not aadhaar: issues.append("Could not find a 12-digit dummy Aadhaar number.")
        elif not id_ok: issues.append("The Aadhaar checksum did not pass. Use a generated dummy test number only.")
        else: masked_id="XXXX XXXX "+re.sub(r"\s+","",aadhaar.group(1))[-4:]
    dob_match=re.search(r"\b(?:DOB|DATE OF BIRTH)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",extracted,re.I)
    if dob_match:
        raw_dob=dob_match.group(1); year=raw_dob[:4] if re.fullmatch(r"\d{4}-\d{2}-\d{2}",raw_dob) else raw_dob[-4:]
        dob_masked="**/**/"+year
    else: dob_masked=None
    quality_ok=(is_pdf and bool(extracted.strip())) or (quality is not None and quality>=50 and not any(x.startswith(("Photo is blurry","Photo is too dark","Photo has glare","Document edges")) for x in issues))
    passed=quality_ok and bool(extracted_name and score>=0.78) and id_ok
    retry=session["retry_count"]+(0 if passed else 1); state="OFFER" if passed else "KYC_RETRY" if retry<3 else "HANDOFF"
    masked=mask_name(extracted_name) if extracted_name else ""
    doc_id=save_document(session_id,kind,quality or 0,masked,score,"PASS" if passed else "RETRY",issues)
    update_session(session_id,state=state,retry_count=retry)
    failure_flags=[]
    if not passed:
        for flag,needle in (("blurry_photo","blurry"),("low_resolution","Resolution is low"),("glare","glare"),("name_mismatch","Name does not match"),("pan_format","PAN format"),("aadhaar_checksum","checksum did not pass"),("ocr_no_text","Could not read a name")):
            if any(needle.lower() in issue.lower() for issue in issues): failure_flags.append(flag)
        if not failure_flags: failure_flags=["document_check_failed"]
    detail=f"quality={quality if quality is not None else 'pdf'};match={score:.2f};ocr={ocr_method};attempt={retry};failure_codes={','.join(failure_flags) if failure_flags else 'none'}"
    log_event(session_id,"KYC","step_completed" if passed else "step_failed","success" if passed else "failure",detail)
    ticket=None
    if state=="HANDOFF":
        ticket=create_handoff(session_id,"three_document_check_failures",f"profile={session['profile_id']};attempts={retry};latest_issues={issues}")
        update_session(session_id,state="HANDOFF")
    offer=None
    if passed:
        session=get_session(session_id); decision=session["decision"]
        from .agent import build_offer
        offer=build_offer(decision,session.get("tenure_months") or 12)
        from .store import save_offer
        save_offer(session_id,offer); log_event(session_id,"OFFER","step_started","success")
    return {"document_id":doc_id,"doc_type":kind,"quality_score":round(quality,1) if quality is not None else None,"quality_metrics":metrics,"ocr_method":ocr_method,"extracted_name_masked":masked,"extracted_id_masked":masked_id,"dob_extracted":bool(dob_match),"dob_masked":dob_masked,"name_match_score":round(score,3),"issues":issues,"passed":passed,"retry_count":retry,"state":state,"ticket_id":ticket,"offer":offer,"demo_only":True,"message":("Sample document passed quality and name-match checks. Review the offer." if passed else "Document needs attention. This is a simulated check and does not verify identity.")}
