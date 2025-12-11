
from datetime import datetime

def _priority_score(intervention):
    score = 0
    priority = (intervention.get("priority") or "").lower()
    status = (intervention.get("status") or "").lower()
    scheduled = intervention.get("scheduled_date") or ""
    created_at = intervention.get("created_at")

    if priority == "high":
        score += 30
    elif priority == "medium":
        score += 15
    else:
        score += 5

    if status in ("open", "in_progress"):
        score += 20
    else:
        score -= 10

    # older interventions first
    try:
        created = datetime.fromisoformat(created_at)
        age_days = (datetime.utcnow() - created).days
        score += min(20, age_days)
    except Exception:
        pass

    # scheduled today or overdue
    if scheduled:
        try:
            sd = datetime.fromisoformat(scheduled)
            delta = (sd - datetime.utcnow()).days
            if delta <= 0:
                score += 20
            elif delta <= 2:
                score += 10
        except Exception:
            pass

    return score

def suggest_priorities(interventions):
    """
    Very simple "AI" engine that ranks interventions and labels them:
    - CRITICAL, HIGH, NORMAL, LOW
    """
    annotated = []
    for it in interventions:
        s = _priority_score(it)
        if s >= 60:
            label = "CRITICAL"
        elif s >= 40:
            label = "HIGH"
        elif s >= 25:
            label = "NORMAL"
        else:
            label = "LOW"
        it2 = dict(it)
        it2["_score"] = s
        it2["_ai_label"] = label
        annotated.append(it2)
    annotated.sort(key=lambda x: x["_score"], reverse=True)
    return annotated
