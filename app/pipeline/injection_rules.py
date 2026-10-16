"""Rule table for the prompt-injection screen in ``injection.py``.

Every pattern runs against the normalised view of one line: case-folded, with
invisible characters removed and Arabic letter variants unified. So the English
patterns are lower case and the Arabic ones use the unified letter forms (no
harakat or tatweel, alef/yaa/taa-marbuta folded).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

HIGH = "high"
MEDIUM = "medium"
NONE = "none"

OVERRIDE = "instruction_override"
ROLE = "role_hijack"
SCORE = "score_manipulation"
MARKER = "fake_role_marker"
HIDDEN = "hidden_text"

# A directive preceded by a negation reports a value; it is not an order.
_NEGATION = re.compile(
    r"(?:\b(?:not|never|no|without|don't|dont|didn't|didnt|won't|cannot|can't)"
    r"|(?:^|\s)(?:لا|عدم|دون|بدون|لم|لن))\s+(?:\S+\s+)?$"
)


def _not_negated(view: str, start: int) -> bool:
    """'never ignore safety instructions' reports a value, it is not an order."""
    return not _NEGATION.search(view[max(0, start - 24) : start])


@dataclass(frozen=True)
class Rule:
    category: str
    severity: str
    rx: re.Pattern[str]
    check: Callable[[str, int], bool] | None = None


def _r(
    category: str, severity: str, pattern: str, check: Callable[[str, int], bool] | None = None
) -> Rule:
    return Rule(category, severity, re.compile(pattern), check)


# ---- English vocabulary (all lower case: the view is case-folded) ----
_DET = r"(?:all|any|every|each|of|the|your|my|these|those|its)"
_TEMPORAL = (
    r"(?:previous|prior|preceding|foregoing|earlier|above|original|initial|existing|"
    r"system|developer|hidden|default|screening|evaluation|ranking|scoring|other)"
)
_OBJ = (
    r"(?:instructions?|prompts?|rules|directions|directives?|commands|guidelines|guidance|"
    r"context|constraints|programming|policies|criteria|rubrics?|scorecards?|requirements|"
    r"system\s+prompt)"
)
_AI = (
    r"(?:ai|a\.i\.|llm|large\s+language\s+model|language\s+model|chat\s?bot|chatgpt|gpt|"
    r"claude|gemini|copilot|ai\s+(?:assistant|model|system|recruiter|screener|agent)|"
    r"(?:screening|recruiting|recruitment|hiring|resume|cv)\s+(?:ai|bot|model|system|"
    r"software|tool|algorithm|assistant)|applicant\s+tracking\s+system|ats|model|bot)"
)
_TARGET = (
    r"(?:me|myself|this\s+(?:candidate|applicant|resume|cv|profile|application|submission|"
    r"person|individual)|the\s+(?:candidate|applicant)(?=\s*(?:[.,;:!]|$|as\b|at\b|with\b|"
    r"to\b|first\b|highest\b|top\b|above\b))|my\s+(?:resume|cv|profile|application|candidacy|"
    r"submission)|him|her)"
)
_TOP = (
    r"(?:top|highest|best|first|1st|number\s+one|no\.?\s*1|#\s*1|perfect|maximum|max|full|"
    r"excellent|outstanding|ideal|strongest|strong\s+(?:hire|yes|fit|match)|must[- ]hire|"
    r"(?:a\s+)?100\s*%|\d{2,3}\s*/\s*\d{2,3}|10\s*/\s*10|5\s*/\s*5|five\s+stars?|a\+|"
    r"(?:above|ahead\s+of|higher\s+than|over|before)\s+(?:all|every(?:one|body)?|any|the\s+"
    r"other|other))"
)
_RANK_V = (
    r"(?:rate|score|rank|grade|mark|evaluate|assess|classify|label|list|place|put|position|"
    r"consider|treat|flag|tag|regard)"
)
# An addressee phrase must END at the model ("note to the AI: …", "if you are an
# AI reading this"), so "attention to the model metrics" or "a reminder for the
# AI team" never read as addressing the screener.
_ADDR_END = (
    r"(?=\s*(?:[:,.;!?—–-]|$)|\s+(?:and|that|who|which|reading|screening|reviewing|"
    r"evaluating|processing|parsing|assessing|analy[sz]ing|here|now)\b)"
)
_QUOTE = r"[\"'“”‘’]?"

RULES: list[Rule] = [
    # ---------------- instruction override ----------------
    _r(
        OVERRIDE,
        HIGH,
        rf"\b(?:ignore|disregard|forget|override|bypass|discard)\s+(?:(?:{_DET}|{_TEMPORAL})\s+){{0,4}}{_OBJ}\b",
        _not_negated,
    ),
    _r(
        OVERRIDE,
        HIGH,
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+(?:of\s+)?)?(?:the\s+|everything\s+)?"
        r"(?:above|previous|prior|preceding|foregoing|earlier)\b",
        _not_negated,
    ),
    _r(
        OVERRIDE,
        HIGH,
        r"\bforget\s+(?:everything|all)\s+(?:you\s+(?:know|were\s+told|have\s+been\s+told)|"
        r"above|before|previous|prior|so\s+far)\b",
    ),
    _r(
        OVERRIDE,
        HIGH,
        # a document claiming authority over the screening: 'this resume overrides
        # all other evaluation criteria'
        r"\b(?:this|my)\s+(?:resume|cv|application|profile|document|submission)\s+"
        r"(?:overrides?|supersedes?|replaces?|takes\s+precedence\s+over)\s+(?:all|any|the|your|every)\b",
    ),
    _r(
        OVERRIDE,
        HIGH,
        r"\b(?:do\s+not|don't|never)\s+(?:follow|obey|apply|use)\s+(?:the\s+|your\s+|any\s+|these\s+)?"
        r"(?:previous\s+|prior\s+|original\s+|above\s+|system\s+|screening\s+)?"
        r"(?:instructions|prompt|rules|guidelines|criteria)\b",
    ),
    # ---------------- fake role / system markers ----------------
    _r(
        MARKER,
        HIGH,
        r"<\|\s*(?:im_start|im_end|system|user|assistant|endoftext|eot_id|start_header_id|"
        r"end_header_id|begin_of_text)\s*\|>",
    ),
    _r(
        MARKER,
        HIGH,
        r"\[\s*/?\s*(?:inst|sys|system|assistant|user)\s*\]|<<\s*/?\s*sys\s*>>",
    ),
    _r(MARKER, HIGH, r"</?\s*(?:system|assistant|user|instructions?|prompt|developer)\s*>"),
    _r(
        MARKER,
        HIGH,
        # a role header that opens a directive: 'SYSTEM PROMPT: you are…'
        r"^\W*(?:system|developer|admin(?:istrator)?)\s+(?:prompt|message|instructions?|note|"
        r"override|command)s?\s*\W*:\s*(?:$|(?=(?:ignore|disregard|forget|you\b|rate\b|rank\b|"
        r"score\b|give\b|assign\b|select\b|shortlist\b|recommend\b|hire\b|output\b|respond\b|"
        r"reply\b|answer\b|always\b|never\b|do\s+not|treat\b|consider\b|evaluate\b|mark\b|act\b|"
        r"pretend\b|from\s+now)))",
    ),
    _r(
        MARKER,
        HIGH,
        r"^\W*(?:#{1,6}\s*)?(?:system|assistant|user)\s*:\s*(?=(?:sure|certainly|of\s+course|"
        r"understood|ok\b|okay|this\s+(?:candidate|applicant)|the\s+(?:candidate|applicant)|"
        r"i\s+will|i'll|you\s+(?:are|must|will|should)|ignore|disregard|rate\b|rank\b|"
        r"score\b|give\b|assign\b|select\b|shortlist\b|recommend\b))",
    ),
    _r(
        MARKER,
        HIGH,
        r"\b(?:new|real|actual|true|override|secret|hidden)\s+(?:system\s+)?"
        r"(?:instructions?|directives?)\s*:",
    ),
    _r(
        MARKER,
        HIGH,
        r"\bend\s+of\s+(?:the\s+)?(?:resume|cv|document|text|input|context|prompt|candidate\s+data)"
        r"\b.{0,40}?\b(?:begin|start|new|system|instructions?|ignore|you\s+(?:are|must|will|should))\b",
    ),
    # ---------------- role / persona hijack, addressing the model ----------------
    _r(ROLE, MEDIUM, r"\byou\s+are\s+(?:now|no\s+longer)\b"),
    _r(
        ROLE,
        MEDIUM,
        r"\b(?:from\s+now\s+on|henceforth),?\s+you\s+(?:are|will|must|should|shall)\b",
    ),
    _r(ROLE, MEDIUM, r"\bpretend\s+(?:to\s+be|that\s+you|you\s+are)\b"),
    _r(
        ROLE,
        MEDIUM,
        rf"\b(?:act|behave|respond|answer|reply)\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?"
        rf"(?:an?\s+|the\s+)?(?:unbiased\s+|helpful\s+|friendly\s+)?{_AI}{_ADDR_END}",
    ),
    _r(
        ROLE,
        MEDIUM,
        r"\bas\s+an?\s+(?:ai|artificial\s+intelligence|language\s+model|llm|chatbot|"
        r"ai\s+(?:assistant|model|system)|assistant)\b(?:\s+\w+){0,2}\s*,\s*you\b",
    ),
    _r(
        ROLE,
        MEDIUM,
        rf"\b(?:note|message|reminder)\s+(?:to|for)\s+(?:the\s+|any\s+|all\s+)?{_AI}{_ADDR_END}",
    ),
    _r(
        ROLE,
        MEDIUM,
        # 'instructions for the model' is also how prompt engineers describe their
        # work, so only the heading form ending in a colon addresses the screener
        rf"\binstructions?\s+(?:to|for)\s+(?:the\s+|any\s+|all\s+)?{_AI}\s*:",
    ),
    _r(ROLE, MEDIUM, rf"\b(?:dear|hey|hi|hello|attention|attn)\s*[,:]?\s+{_AI}{_ADDR_END}"),
    _r(ROLE, MEDIUM, rf"^\W*to\s+(?:the\s+|any\s+|all\s+|every\s+)?{_AI}{_ADDR_END}"),
    _r(
        ROLE,
        MEDIUM,
        # vocative to a screening bot ('Recruiter bot, listen: …'); restricted to
        # bot/AI compounds so 'Hiring system: Workday' stays a skills line
        r"^\W*(?:screening|recruiting|recruitment|recruiter|hiring|resume|cv|hr)\s+(?:ai|bot)\s*,",
    ),
    _r(
        ROLE,
        MEDIUM,
        rf"\bif\s+you\s+are\s+(?:an?\s+|the\s+)?(?:automated\s+)?(?:{_AI}|automated\s+\w+){_ADDR_END}",
    ),
    _r(
        ROLE,
        HIGH,
        r"\b(?:enable|enter|activate|switch\s+to)\s+(?:dan|jailbreak|god|unrestricted)\s+mode\b",
    ),
    # ---------------- ranking / score manipulation ----------------
    _r(
        SCORE,
        HIGH,
        rf"\b{_RANK_V}\s+{_TARGET}\s+(?:as\s+|at\s+|with\s+|in\s+|to\s+)?(?:an?\s+|the\s+|our\s+)?{_TOP}",
    ),
    _r(
        SCORE,
        HIGH,
        rf"\b(?:give|assign|award|grant)\s+(?:{_TARGET}\s+|it\s+)?(?:an?\s+|the\s+)?"
        rf"(?:highest|perfect|top|maximum|max|full|best|10\s*/\s*10|100\s*%|five[- ]star|5[- ]star|"
        rf"a\+)\s+(?:possible\s+)?(?:score|rating|rank|ranking|mark|marks|grade|match|fit|"
        rf"evaluation|review|recommendation)\b",
    ),
    _r(
        SCORE,
        HIGH,
        rf"\b(?:give|assign|award|grant|set|output|return)\s+(?:{_TARGET}\s+|it\s+)?"
        rf"(?:an?\s+|the\s+|their\s+|my\s+)?(?:fit\s+|match\s+|final\s+|overall\s+)?"
        rf"(?:score|rating|grade|mark|rank(?:ing)?)\s+(?:of\s+|to\s+|as\s+|=\s*|:\s*)?"
        rf"(?:100\s*%?|10\s*/\s*10|5\s*/\s*5|#?\s*1\b|first\b|perfect|maximum|max\b|highest)",
    ),
    _r(
        SCORE,
        HIGH,
        rf"\b(?:output|return|respond|reply|answer|print)\s+(?:only\s+)?(?:with\s+)?:?\s*"
        rf"{_QUOTE}\s*(?:strong\s+(?:hire|yes|fit|match)|perfect\s+(?:fit|match|candidate|score)|"
        rf"hire\s+(?:this|immediately))",
    ),
    _r(
        SCORE,
        HIGH,
        r"\b(?:output|return|respond|reply|print|say)\s+(?:only\s+)?(?:with\s+)?:?\s*[\"'“‘]\s*"
        r"(?:hire|strong\s+hire|yes|accept(?:ed)?|approved?|shortlist(?:ed)?|selected)\s*[\"'”’]",
    ),
    _r(
        SCORE,
        HIGH,
        rf"\b(?:print|output|show|display|return|report)\s+(?:the\s+|a\s+)?(?:highest|perfect|"
        rf"maximum|max|top|best|full)\s+(?:possible\s+)?(?:score|rating|rank|grade|mark|marks)\s+"
        rf"(?:for|to)\s+{_TARGET}",
    ),
    _r(
        SCORE,
        HIGH,
        r"\b(?:output|return|respond|reply|print)\b.{0,40}?\b(?:score|rating|fit|decision|"
        r"verdict|recommendation)\b\W{0,4}\s*(?:100|10\s*/\s*10|hire|strong\s+hire|perfect)\b",
    ),
    _r(
        SCORE,
        HIGH,
        r"\b(?:do\s+not|don't|never)\s+(?:reject|screen\s+out|filter\s+out|disqualify|discard|"
        r"penali[sz]e|exclude|down-?rank|deprioriti[sz]e|ignore|overlook|skip)\s+(?:me|this\s+"
        r"(?:candidate|applicant|application|cv|resume)|my\s+(?:application|cv|resume|candidacy))\b",
    ),
    _r(
        SCORE,
        HIGH,
        r"\b(?:this\s+(?:candidate|applicant)|the\s+(?:candidate|applicant)|i|he|she)\s+"
        r"(?:must|should|has\s+to|needs\s+to|will)\s+be\s+(?:ranked|rated|scored|placed|listed)\s+"
        r"(?:as\s+)?(?:the\s+)?(?:first|highest|top|#?\s*1\b|number\s+one|100|perfect|above)",
    ),
    _r(
        SCORE,
        HIGH,
        r"\b(?:this\s+(?:candidate|applicant)|the\s+(?:candidate|applicant))\s+(?:must|has\s+to)\s+"
        r"be\s+(?:hired|selected|shortlisted|chosen|recommended|approved|advanced)\b",
    ),
    _r(
        SCORE,
        MEDIUM,
        r"\b(?:select|shortlist|short-list|recommend|approve|hire|advance|prioriti[sz]e)\s+"
        r"(?:me|this\s+(?:candidate|applicant|cv|resume|application)|my\s+(?:application|cv|"
        r"resume|candidacy))\b",
    ),
    _r(
        SCORE,
        MEDIUM,
        r"\b(?:this\s+(?:candidate|applicant)|the\s+(?:candidate|applicant))\s+(?:is|should\s+be|"
        r"will\s+be|would\s+be)\s+(?:the\s+|an?\s+)?(?:best|top|ideal|perfect|strongest|most\s+"
        r"qualified|number\s+one|#\s*1|first)\b",
    ),
    _r(
        SCORE,
        HIGH,
        rf"\b{_RANK_V}\s+{_TARGET}\s+as\s+(?:meeting|satisfying|fulfilling|matching)\s+(?:every|all|each)"
        rf"\s+(?:of\s+the\s+)?(?:requirements?|criteri(?:on|a)|skills?|qualifications?)\b",
    ),
    _r(
        SCORE,
        MEDIUM,
        r"\b(?:candidate|applicant|resume|cv|profile|application)\s+(?:is|has\s+been)\s+(?:already\s+)?"
        r"(?:pre-?approved|pre-?screened|pre-?selected|pre-?verified|approved\s+by\s+(?:hr|the\s+hiring))\b",
    ),
    _r(
        SCORE,
        MEDIUM,
        r"\bthis\s+is\s+(?:the|an?)\s+(?:ideal|perfect|best|top|strongest)\s+(?:candidate|applicant|hire)\b",
    ),
    _r(
        SCORE,
        HIGH,
        r"\b(?:mark|rate|score|treat|consider|count)\s+(?:every|all|each|any)\s+(?:of\s+(?:the|my)\s+)?"
        r"(?:skills?|requirements?|criteri(?:on|a)|qualifications?)\b.{0,40}?\b(?:as\s+)?(?:fully\s+)?"
        r"(?:met|satisfied|matched|present|fulfilled)\b",
    ),
    # ---------------- Arabic (normalised: no harakat/tatweel, unified letters) ----------------
    _r(
        OVERRIDE,
        HIGH,
        r"(?<!\w)(?:و|ف)?(?:تجاهل|اهمل|انس|انسي|تخط|الغ)(?:ي|وا)?\s+(?:(?:جميع|كل|كافه|اي|بقيه)\s+)?"
        r"(?:ما\s+(?:سبق|ورد|ذكر|تقدم)|(?:ال)?(?:تعليمات|اوامر|توجيهات|قواعد|ارشادات|معايير|ضوابط))",
        _not_negated,
    ),
    _r(
        OVERRIDE,
        HIGH,
        r"(?<!\w)(?:لا|عدم)\s+(?:تلتزم|تلتزمي|تتبع|تتبعي|تطبق|تطبقي|الالتزام|اتباع)\s+"
        r"(?:ب|ل)?(?:ال)?(?:تعليمات|اوامر|توجيهات|قواعد|معايير)\s+(?:ال)?(?:سابقه|اصليه|اعلاه|"
        r"المذكوره|المعطاه)",
    ),
    _r(
        MARKER,
        HIGH,
        r"(?<!\w)(?:تعليمات|رساله|اوامر|موجه)\s+(?:ال)?نظام\s*:|(?<!\w)(?:تعليمات|اوامر)\s+جديده\s*:",
    ),
    _r(ROLE, MEDIUM, r"(?<!\w)انت\s+الان(?!\w)"),
    _r(ROLE, MEDIUM, r"(?<!\w)من\s+الان\s+(?:فصاعدا|وصاعدا)\s*،?\s*(?:انت|عليك|يجب)"),
    _r(ROLE, MEDIUM, r"(?<!\w)تظاهر(?:ي)?\s+(?:بانك|انك)"),
    _r(
        ROLE,
        MEDIUM,
        r"(?<!\w)تصرف(?:ي)?\s+(?:ك|بصفتك\s+)(?:مساعد|نموذج|ذكاء|مقيم|مسؤول\s+توظيف)",
    ),
    _r(
        ROLE,
        MEDIUM,
        r"(?<!\w)(?:ملاحظه|رساله|تنبيه)\s+(?:لل|الي\s+ال|الي)(?:ذكاء\s+الاصطناعي|نموذج|مساعد|"
        r"نظام\s+(?:الفرز|التوظيف|التقييم))",
    ),
    _r(
        ROLE,
        MEDIUM,
        r"(?<!\w)(?:الي|ايها|يا)\s+(?:ال)?(?:نموذج|ذكاء\s+الاصطناعي|مساعد\s+الذكي|نظام\s+"
        r"(?:الفرز|التوظيف|التقييم))(?:\s+(?:ال)?(?:ذكاء\s+الاصطناعي|لغوي))?\s*[:،,]",
    ),
    _r(
        SCORE,
        HIGH,
        r"(?<!\w)(?:و|ف)?(?:قيم|اعط|امنح|ضع|صنف|رتب|اعتبر|اجعل|سجل)(?:ي|وا)?\s+"
        r"(?:هذا\s+(?:المرشح|المتقدم)|هذه\s+(?:المرشحه|المتقدمه|السيره(?:\s+الذاتيه)?)|"
        r"(?:ال)?(?:مرشح|متقدم)\s+هذا)\s+(?:في\s+)?(?:(?:ب|ك)?(?:اعلي|افضل|الافضل|الاول|الاولي)|"
        r"(?:ال)?(?:مرتبه|درجه|علامه|تقييم)\s+(?:ال)?(?:اولي|كامله|قصوي|اعلي|الاعلي)|100)",
    ),
    _r(
        SCORE,
        HIGH,
        r"(?<!\w)(?:و|ف)?(?:ارفع|ارفعي|اجعل|اجعلي)\s+(?:تقييم|درجه|ترتيب)\s+(?:هذا\s+(?:المرشح|المتقدم)|"
        r"هذه\s+(?:المرشحه|المتقدمه))\s+(?:الي\s+)?(?:100|اعلي|الاعلي|الحد\s+الاقصي)",
    ),
    _r(
        SCORE,
        HIGH,
        r"(?<!\w)يجب\s+(?:ان\s+)?(?:قبول|توظيف|اختيار|ترشيح|تعيين|يقبل|يوظف|يختار|يرشح)\s+"
        r"(?:هذا\s+(?:المرشح|المتقدم)|هذه\s+(?:المرشحه|المتقدمه))",
    ),
    _r(
        SCORE,
        HIGH,
        r"(?<!\w)لا\s+(?:ترفض|ترفضي|تستبعد|تستبعدي|تتجاهل|تتجاهلي)\s+(?:هذا\s+(?:المرشح|المتقدم)|"
        r"هذه\s+(?:المرشحه|المتقدمه|السيره))",
    ),
    _r(
        SCORE,
        MEDIUM,
        r"(?<!\w)(?:يوصي|توصي|اوصي)\s+(?:دايما\s+)?ب(?:هذا\s+(?:المرشح|المتقدم)|هذه\s+(?:المرشحه|المتقدمه))",
    ),
]

# Compact signatures checked on a squashed view (all non-letters removed) when a
# line is letter-spaced or split by invisible characters: 'i g n o r e …',
# 'ignore\u200ball\u200bprevious…'. Any hit there is obfuscated by definition.
SQUASHED_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        OVERRIDE,
        re.compile(
            r"(?:ignore|disregard|forget|override)(?:all|any|every|the|your|of)*"
            r"(?:previous|prior|above|earlier|preceding|foregoing|original|system)?"
            r"(?:instructions?|prompts?|rules|directions|guidelines|criteria)"
        ),
    ),
    (
        OVERRIDE,
        re.compile(r"(?:ignore|disregard|forget)(?:all|the|everything)*(?:above|previous|prior)"),
    ),
    (
        SCORE,
        re.compile(
            r"(?:rate|rank|score|mark|place)(?:this|the)?(?:candidate|applicant|me)"
            r"(?:as|at)?(?:the|a)?(?:top|first|highest|best|perfect|100)"
        ),
    ),
    (
        SCORE,
        re.compile(
            r"(?:give|assign|award)(?:this|the)?(?:candidate|applicant|me)?(?:the|a)?"
            r"(?:highest|perfect|maximum|top|full)(?:score|rating|rank|marks?)"
        ),
    ),
    (ROLE, re.compile(r"youarenow|pretendyouare|fromnowonyou")),
    (OVERRIDE, re.compile(r"(?:تجاهل|اهمل|انس)(?:جميع|كل|كافه)?(?:ال)?(?:تعليمات|اوامر|توجيهات)")),
]
