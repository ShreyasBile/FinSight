"""Synthetic Indian applicant identities with internally consistent documents.

Every value here is invented: names come from common-name lists, PAN/Aadhaar/
GSTIN/IFSC are generated to be FORMAT-valid (Aadhaar passes Verhoeff) but are
not anyone's real number. The generator is seeded, so a dataset rebuilt from
the same seed is byte-identical — results stay reproducible across runs.
"""
from __future__ import annotations

import random
import string
import zlib
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

FIRST_M = ["Rahul", "Amit", "Suresh", "Vikram", "Arjun", "Rohan", "Karan", "Manoj",
           "Sanjay", "Deepak", "Nikhil", "Pradeep", "Rajesh", "Anil", "Harish", "Gaurav",
           "Mohit", "Varun", "Ajay", "Naveen", "Tarun", "Yash", "Kunal", "Siddharth"]
FIRST_F = ["Priya", "Neha", "Anjali", "Pooja", "Kavita", "Sunita", "Meena", "Ritu",
           "Swati", "Divya", "Shreya", "Nisha", "Aarti", "Rekha", "Sneha", "Komal",
           "Pallavi", "Ishita", "Tanvi", "Megha"]
SURNAMES = ["Sharma", "Verma", "Gupta", "Patel", "Singh", "Kumar", "Reddy", "Nair",
            "Iyer", "Joshi", "Mehta", "Agarwal", "Chauhan", "Yadav", "Mishra", "Pandey",
            "Desai", "Kulkarni", "Bose", "Das", "Rao", "Menon", "Saxena", "Bhatt"]
CITIES = [("Jaipur", "Rajasthan", "302"), ("Lucknow", "Uttar Pradesh", "226"),
          ("Pune", "Maharashtra", "411"), ("Indore", "Madhya Pradesh", "452"),
          ("Patna", "Bihar", "800"), ("Bengaluru", "Karnataka", "560"),
          ("Chennai", "Tamil Nadu", "600"), ("Ahmedabad", "Gujarat", "380"),
          ("Kochi", "Kerala", "682"), ("Bhopal", "Madhya Pradesh", "462")]
STATE_GST = {"Rajasthan": "08", "Uttar Pradesh": "09", "Maharashtra": "27",
             "Madhya Pradesh": "23", "Bihar": "10", "Karnataka": "29",
             "Tamil Nadu": "33", "Gujarat": "24", "Kerala": "32"}
STREETS = ["MG Road", "Station Road", "Civil Lines", "Gandhi Nagar", "Shastri Nagar",
           "Nehru Colony", "Tilak Marg", "Ashok Vihar", "Sector 12", "Vaishali Nagar"]
EMPLOYERS = ["Sunrise Logistics Pvt Ltd", "Meridian Software Services Pvt Ltd",
             "Kaveri Textiles Ltd", "Northstar Engineering Works Pvt Ltd",
             "Bluewave Retail India Pvt Ltd", "Trident Pharma Ltd",
             "Greenfield Agro Industries Pvt Ltd", "Apex Infra Projects Ltd"]
DESIGNATIONS = ["Assistant Manager", "Senior Executive", "Software Engineer",
                "Accountant", "Sales Officer", "Operations Lead", "Team Lead", "Analyst"]
BANKS = [("State Bank of India", "SBIN"), ("HDFC Bank", "HDFC"), ("ICICI Bank", "ICIC"),
         ("Axis Bank", "UTIB"), ("Punjab National Bank", "PUNB"), ("Bank of Baroda", "BARB"),
         ("Kotak Mahindra Bank", "KKBK"), ("Canara Bank", "CNRB")]
BUSINESSES = ["Traders", "Enterprises", "Stores", "Agencies", "Industries"]

# ── Verhoeff (Aadhaar checksum) ─────────────────────────────────────────────
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
      [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
      [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def make_aadhaar(rng: random.Random) -> str:
    # UIDAI numbers never start with 0 or 1, and are not sequential/repeated.
    while True:
        body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
        num = body + verhoeff_check_digit(body)
        if len(set(num)) > 3:
            return num


def make_pan(rng: random.Random, surname: str, kind: str = "P") -> str:
    letters = "".join(rng.choice(string.ascii_uppercase) for _ in range(3))
    return f"{letters}{kind}{surname[0].upper()}{rng.randint(1000, 9999)}{rng.choice(string.ascii_uppercase)}"


def make_ifsc(rng: random.Random, prefix: str) -> str:
    return f"{prefix}0{rng.randint(0, 999999):06d}"


def make_gstin(rng: random.Random, pan: str, state: str) -> str:
    code = STATE_GST.get(state, "27")
    return f"{code}{pan}{rng.randint(1, 9)}Z{rng.choice(string.ascii_uppercase + string.digits)}"


def stable(s: str) -> int:
    """Process-independent hash (built-in hash() is salted per interpreter run,
    which would make rebuilt datasets differ)."""
    return zlib.crc32(s.encode("utf-8"))


def fmt_inr(n: float, decimals: bool = True) -> str:
    """Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    neg = n < 0
    n = abs(n)
    whole = int(n)
    frac = f"{n - whole:.2f}"[1:] if decimals else ""
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if neg else "") + s + frac


@dataclass
class Transaction:
    date: str           # DD/MM/YYYY
    description: str
    debit: float
    credit: float
    balance: float


@dataclass
class Identity:
    uid: str
    full_name: str
    first_name: str
    surname: str
    gender: str
    father_name: str
    dob: str                     # DD/MM/YYYY
    address: str
    city: str
    state: str
    pincode: str
    pan: str
    aadhaar: str
    employer: str
    designation: str
    basic: int
    hra: int
    special_allowance: int
    gross: int
    pf: int
    professional_tax: int
    tds: int
    net: int
    month_year: str
    bank_name: str
    ifsc: str
    account_number: str
    statement_period: str
    opening_balance: float
    closing_balance: float
    total_credits: float
    total_debits: float
    average_balance: float
    transactions: list = field(default_factory=list)
    assessment_year: str = ""
    gross_total_income: int = 0
    annual_tds: int = 0
    employer_tan: str = ""
    gstin: str = ""
    trade_name: str = ""
    turnover: int = 0
    gst_tax_paid: int = 0
    gst_period: str = ""
    cibil_score: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _statement(rng: random.Random, net: int, months: int = 3,
               start: date = date(2026, 4, 1)) -> tuple[list[Transaction], dict]:
    """A realistic statement: monthly salary credit, UPI spends, bills, an ATM
    withdrawal or two. Balances reconcile exactly (running balance)."""
    bal = float(rng.randint(15_000, 180_000))
    opening = bal
    tx: list[Transaction] = []
    day = start
    end = start + timedelta(days=30 * months)
    balances = []
    while day < end:
        events = []
        if day.day == 1:
            events.append(("SALARY CREDIT NEFT", 0.0, float(net)))
        if day.day in (5, 18):
            events.append((f"UPI/{rng.randint(100000, 999999)}/GROCERY", float(rng.randint(800, 4500)), 0.0))
        if day.day == 10:
            events.append(("ELECTRICITY BILL PAYMENT", float(rng.randint(900, 3200)), 0.0))
        if day.day == 12:
            events.append((f"ATM WDL {rng.randint(1000, 9999)}", float(rng.choice([2000, 5000, 10000])), 0.0))
        if day.day == 15:
            events.append(("RENT TRANSFER IMPS", float(rng.randint(8000, 22000)), 0.0))
        if day.day == 25:
            events.append((f"UPI/{rng.randint(100000, 999999)}/SHOPPING", float(rng.randint(500, 6000)), 0.0))
        for desc, dr, cr in events:
            bal = round(bal - dr + cr, 2)
            tx.append(Transaction(day.strftime("%d/%m/%Y"), desc, dr, cr, bal))
        balances.append(bal)
        day += timedelta(days=1)
    credits = round(sum(t.credit for t in tx), 2)
    debits = round(sum(t.debit for t in tx), 2)
    summary = {
        "opening_balance": opening,
        "closing_balance": bal,
        "total_credits": credits,
        "total_debits": debits,
        "average_balance": round(sum(balances) / len(balances), 2),
        "statement_period": f"{start.strftime('%d/%m/%Y')} to {(end - timedelta(days=1)).strftime('%d/%m/%Y')}",
    }
    return tx, summary


def make_identity(rng: random.Random, uid: str) -> Identity:
    gender = rng.choice(["Male", "Female"])
    first = rng.choice(FIRST_M if gender == "Male" else FIRST_F)
    surname = rng.choice(SURNAMES)
    father = f"{rng.choice(FIRST_M)} {surname}"
    dob = date(rng.randint(1970, 2001), rng.randint(1, 12), rng.randint(1, 28))
    city, state, pin3 = rng.choice(CITIES)
    pincode = f"{pin3}{rng.randint(0, 999):03d}"
    address = f"{rng.randint(1, 250)}, {rng.choice(STREETS)}, {city}, {state} - {pincode}"
    pan = make_pan(rng, surname)
    basic = rng.randrange(18_000, 90_000, 500)
    hra = int(basic * 0.4)
    special = rng.randrange(2_000, 25_000, 500)
    gross = basic + hra + special
    pf = int(basic * 0.12)
    ptax = 200
    tds = int(max(0, gross - 50_000) * 0.1)
    net = gross - pf - ptax - tds
    bank, prefix = rng.choice(BANKS)
    tx, summ = _statement(rng, net)
    trade = f"{surname} {rng.choice(BUSINESSES)}"
    return Identity(
        uid=uid, full_name=f"{first} {surname}", first_name=first, surname=surname,
        gender=gender, father_name=father, dob=dob.strftime("%d/%m/%Y"),
        address=address, city=city, state=state, pincode=pincode, pan=pan,
        aadhaar=make_aadhaar(rng), employer=rng.choice(EMPLOYERS),
        designation=rng.choice(DESIGNATIONS), basic=basic, hra=hra,
        special_allowance=special, gross=gross, pf=pf, professional_tax=ptax,
        tds=tds, net=net, month_year="March 2026", bank_name=bank,
        ifsc=make_ifsc(rng, prefix), account_number=str(rng.randint(10**10, 10**12 - 1)),
        transactions=[asdict(t) for t in tx],
        assessment_year="AY 2025-26", gross_total_income=gross * 12,
        annual_tds=tds * 12,
        employer_tan=f"{''.join(rng.choice(string.ascii_uppercase) for _ in range(4))}{rng.randint(10000, 99999)}{rng.choice(string.ascii_uppercase)}",
        gstin=make_gstin(rng, pan, state), trade_name=trade,
        turnover=rng.randrange(1_500_000, 9_000_000, 10_000),
        gst_tax_paid=0, gst_period="March 2026",
        cibil_score=rng.randint(560, 860),
        **summ,
    )


def expected_fields(ident: Identity, doc_type: str) -> dict:
    """Ground truth in exactly the shape the extraction prompts ask for
    (api/app/doc_types.py), so extraction output can be compared key by key."""
    i = ident
    if doc_type == "pan":
        return {"pan_number": i.pan, "full_name": i.full_name.upper(), "dob": i.dob,
                "father_name": i.father_name.upper()}
    if doc_type == "aadhaar":
        return {"aadhaar_number": f"{i.aadhaar[:4]} {i.aadhaar[4:8]} {i.aadhaar[8:]}",
                "full_name": i.full_name, "dob": i.dob, "gender": i.gender,
                "address": i.address, "pincode": i.pincode}
    if doc_type == "salary_slip":
        return {"employee_name": i.full_name, "employer_name": i.employer,
                "month_year": i.month_year, "designation": i.designation,
                "basic_pay": i.basic, "hra": i.hra, "gross_salary": i.gross,
                "net_salary": i.net}
    if doc_type == "bank_statement":
        return {"account_holder": i.full_name, "bank_name": i.bank_name,
                "account_number": i.account_number, "ifsc": i.ifsc,
                "statement_period": i.statement_period,
                "opening_balance": i.opening_balance, "closing_balance": i.closing_balance,
                "total_credits": i.total_credits, "total_debits": i.total_debits,
                "average_balance": i.average_balance}
    if doc_type == "itr":
        return {"document_type": "ITR", "name": i.full_name, "assessment_year": i.assessment_year,
                "gross_total_income": i.gross_total_income, "total_tds": i.annual_tds}
    if doc_type == "form16":
        return {"employee_name": i.full_name, "employer_name": i.employer,
                "employer_tan": i.employer_tan, "assessment_year": i.assessment_year,
                "gross_salary": i.gross * 12, "total_tds": i.annual_tds}
    if doc_type == "gst_return":
        return {"gstin": i.gstin, "legal_name": i.full_name, "trade_name": i.trade_name,
                "return_period": i.gst_period, "turnover": i.turnover,
                "tax_paid": int(i.turnover * 0.18)}
    if doc_type == "cibil":
        return {"name": i.full_name, "cibil_score": i.cibil_score, "total_accounts": 5,
                "active_accounts": 2, "settled_accounts": 0, "defaults": 0}
    raise ValueError(doc_type)
