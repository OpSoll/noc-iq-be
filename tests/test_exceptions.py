from app.core.exceptions import ProblemDetails

def test_problem_details_model():
    p = ProblemDetails(type="t", title="t", status=400, detail="d", instance="i")
    assert p.status == 400
