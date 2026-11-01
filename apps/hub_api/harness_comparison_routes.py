from datetime import UTC,datetime
from fastapi import APIRouter,HTTPException,Request,Response
from packages.contracts_py.decision_hub_contracts.generated.harness_comparison import (
    HarnessComparisonCatalog,HarnessComparisonView,HarnessComparisonStart,HarnessComparisonCancel,HarnessOwnerSessionPair,HarnessOwnerSessionReply,
)
from apps.hub_api.frozen_harness_bootstrap import configured_frozen_run_harness

COOKIE='decision_hub_harness_owner'

def harness_comparison_router(database):
    router=APIRouter(prefix='/v1/harness')
    bootstrap=configured_frozen_run_harness(database)
    def available():
        if bootstrap is None:raise HTTPException(503,'harness_frozen_comparison_not_configured')
        return bootstrap
    def principal(request: Request,mutation: bool=False):
        configured=available()
        try:
            return configured.authority.resolve(request.cookies.get(COOKIE,''),origin=request.headers.get('origin'),mutation=mutation,csrf_token=request.headers.get('x-harness-csrf'))
        except PermissionError as error:raise HTTPException(403,str(error)) from None
    def guarded(operation):
        try:return operation()
        except PermissionError as error:raise HTTPException(403,str(error)) from None
        except KeyError:raise HTTPException(404,'harness_frozen_comparison_not_found') from None
        except (ValueError,OSError) as error:
            code=str(error) if isinstance(error,ValueError) and str(error).startswith(('harness_','study_','research_scope_')) else 'harness_frozen_originals_unavailable'
            raise HTTPException(409,code) from None
    @router.get('/comparisons/catalog',response_model=HarnessComparisonCatalog)
    def catalog(request: Request):
        if bootstrap is None:
            return HarnessComparisonCatalog(schema_version='harness-comparison-catalog.v1',available=False,blocked_reason='harness_frozen_comparison_not_configured',owner_authenticated=False,sources=[],methods=[],budget={'per_arm_max_cost_usd':0.0,'total_max_cost_usd':0.0,'total_deadline_seconds':1},eligible_for_deployment=False)
        authenticated=False
        if request.cookies.get(COOKIE):
            try:principal(request);authenticated=True
            except HTTPException:pass
        return guarded(lambda:bootstrap.service.catalog_view(authenticated))
    @router.post('/owner-session',response_model=HarnessOwnerSessionReply)
    def pair(body: HarnessOwnerSessionPair,request: Request,response: Response):
        configured=available()
        session=guarded(lambda:configured.authority.pair(body.pairing_token,origin=request.headers.get('origin','')))
        response.set_cookie(COOKIE,session.cookie_value,max_age=configured.authority.lifetime_seconds,httponly=True,samesite='strict',path=session.cookie_path)
        response.headers['Cache-Control']='no-store'
        return HarnessOwnerSessionReply(csrf_token=session.csrf_token,expires_at=datetime.fromtimestamp(session.expires_at,UTC))
    @router.post('/comparisons',response_model=HarnessComparisonView)
    def start(body: HarnessComparisonStart,request: Request,response: Response):
        actor=principal(request,True);configured=available()
        assignment=guarded(lambda:configured.service.create(catalog_ref=body.catalog_ref,candidate_ref=body.candidate_ref,request_id=body.request_id,principal=actor))
        response.headers['Cache-Control']='no-store'
        return guarded(lambda:configured.service.detail(assignment))
    @router.get('/comparisons/by-request/{request_id}',response_model=HarnessComparisonView|None)
    def by_request(request_id: str,request: Request,response: Response):
        principal(request);configured=available();response.headers['Cache-Control']='no-store'
        assignment=guarded(lambda:configured.service.by_request(request_id))
        return guarded(lambda:configured.service.detail(assignment)) if assignment else None
    @router.get('/comparisons/{assignment_id}',response_model=HarnessComparisonView)
    def detail(assignment_id: str,request: Request,response: Response):
        principal(request);configured=available();response.headers['Cache-Control']='no-store'
        return guarded(lambda:configured.service.detail(assignment_id))
    @router.post('/comparisons/{assignment_id}/cancel',response_model=HarnessComparisonView)
    def cancel(assignment_id: str,body: HarnessComparisonCancel,request: Request,response: Response):
        actor=principal(request,True);configured=available();response.headers['Cache-Control']='no-store'
        guarded(lambda:configured.service.cancel(assignment_id,body.request_id,principal=actor))
        return guarded(lambda:configured.service.detail(assignment_id))
    return router
