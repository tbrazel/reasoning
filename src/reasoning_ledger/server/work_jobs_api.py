"""Scoped human administration and credential-separated work worker endpoints."""

from typing import Annotated

from fastapi import Depends, Request

from .store import StoreError
from .work_controller_contracts import ControllerResume, ControllerStart, HumanCheck
from .work_controllers import WorkControllers
from .work_job_contracts import (
    CompactionNote,
    EffectIntent,
    EffectReceipt,
    EffectReconciliation,
    InputRead,
    JobEnqueue,
    JobLease,
    JobResult,
    ProgramControl,
    SchedulerPolicy,
    TaskReopen,
    WorkerClaim,
    WorkerEnrollment,
)
from .work_jobs import WorkJobs
from .work_provider import WorkProvider
from .work_provider_contracts import WorkCompletion, WorkModelInfo, WorkProgress
from .work_sources import WorkSources
from .work_tools import MODELS, WorkTools, catalog


def install_routes(app, store, authenticated, provider):
    jobs = WorkJobs(store)
    app.state.work_jobs = jobs
    tools = WorkTools(jobs, app.state.compute)
    app.state.work_tools = tools
    sources = WorkSources(tools)
    app.state.work_sources = sources
    broker = WorkProvider(jobs, provider)
    app.state.work_provider = broker
    User = Annotated[str, Depends(authenticated)]
    root = "/api/organizations/{organization_id}"
    project = root + "/projects/{project_id}/work/{program_id}"
    controllers = WorkControllers(store)
    app.state.work_controllers = controllers

    @app.post(project + "/controllers", status_code=201)
    def start_controller(organization_id: str, project_id: str, program_id: str, body: ControllerStart, user: User):
        return controllers.start(user, organization_id, project_id, program_id, body)

    @app.get(project + "/controllers/{controller_id}")
    def read_controller(organization_id: str, project_id: str, program_id: str, controller_id: str, user: User):
        return controllers.read(user, organization_id, project_id, program_id, controller_id)

    @app.get(project + "/controllers/{controller_id}/steps")
    def controller_steps(
        organization_id: str, project_id: str, program_id: str, controller_id: str, user: User, after: int = 0, limit: int = 50
    ):
        return controllers.history(user, organization_id, project_id, program_id, controller_id, after, limit)

    @app.post(project + "/controllers/{controller_id}/checks", status_code=201)
    def human_check(organization_id: str, project_id: str, program_id: str, controller_id: str, body: HumanCheck, user: User):
        return controllers.check(user, organization_id, project_id, program_id, controller_id, body)

    @app.post(project + "/controllers/{controller_id}/cancel")
    def cancel_controller(organization_id: str, project_id: str, program_id: str, controller_id: str, body: ControllerResume, user: User):
        return controllers.cancel(user, organization_id, project_id, program_id, controller_id, body)

    @app.post(project + "/controllers/{controller_id}/resume")
    def resume_controller(organization_id: str, project_id: str, program_id: str, controller_id: str, body: ControllerResume, user: User):
        return controllers.resume(user, organization_id, project_id, program_id, controller_id, body)

    def worker_credential(request: Request):
        scheme, separator, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not separator or not value.startswith("rnaw_") or len(value) != 48:
            raise StoreError(401, "valid work worker credential required")
        return value

    Worker = Annotated[str, Depends(worker_credential)]

    @app.post("/api/work-worker/jobs/{job_id}/computations/{compute_job_id}/wait")
    def computation_wait(job_id: str, compute_job_id: str, body: JobLease, credential: Worker):
        program = jobs._job_scope(credential, job_id)
        # Status reads expire missing compute hosts. Match compute completion's
        # quota-before-program order so that independent polls cannot deadlock.
        with store.transaction(write=True) as db:
            jobs._program_lock(store, db, program)
            job = jobs._lease(db, credential, job_id, body)
            task, root = jobs._authorized_task(db, job)
            tools._admit(db, job, task, root, "compute_submit", replay=True)
            return tools.computations.view(db, task, root, compute_job_id)

    @app.post("/api/work-worker/jobs/{job_id}/model-info")
    async def model_info(job_id: str, body: WorkModelInfo, credential: Worker):
        return await broker.model_info(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/inference")
    async def inference(job_id: str, body: WorkCompletion, credential: Worker):
        return await broker.complete(credential, job_id, body)

    @app.get("/api/work-worker/calls/{call_id}")
    def read_call(call_id: str, credential: Worker):
        return broker.read(credential, call_id)

    @app.post("/api/work-worker/jobs/{job_id}/progress")
    def progress(job_id: str, body: WorkProgress, credential: Worker):
        return broker.progress(credential, job_id, body)

    @app.get(project + "/jobs/{job_id}/events")
    def events(organization_id: str, project_id: str, program_id: str, job_id: str, user: User, after: int = 0, limit: int = 100):
        return broker.events(user, organization_id, project_id, program_id, job_id, after, limit)

    @app.get(root + "/work-tools")
    def tool_catalog(organization_id: str, user: User):
        with store.transaction() as db:
            store._require(db, user, organization_id)
        return catalog()

    def local_endpoint(tool, model):
        def perform(job_id: str, body: model, credential: Worker):
            return tools.execute(credential, job_id, tool, body)

        async def retrieve(job_id: str, body: model, credential: Worker):
            return await sources.execute(credential, job_id, tool, body)

        handler = retrieve if tool in {"source_search", "source_fetch"} else perform
        handler.__name__ = "work_tool_" + tool
        app.post("/api/work-worker/jobs/{job_id}/tools/" + tool)(handler)

    for tool, model in MODELS.items():
        local_endpoint(tool, model)

    @app.get(root + "/work-scheduler-policy")
    def read_policy(organization_id: str, user: User):
        return jobs.policy(user, organization_id)

    @app.put(root + "/work-scheduler-policy")
    def write_policy(organization_id: str, body: SchedulerPolicy, user: User):
        return jobs.policy(user, organization_id, body)

    @app.post(root + "/work-workers", status_code=201)
    def enroll(organization_id: str, body: WorkerEnrollment, user: User):
        return jobs.enroll(user, organization_id, body)

    @app.delete(root + "/work-workers/{worker_id}", status_code=204)
    def revoke(organization_id: str, worker_id: str, user: User):
        jobs.revoke(user, organization_id, worker_id)

    @app.post(project + "/control")
    def control(organization_id: str, project_id: str, program_id: str, body: ProgramControl, user: User):
        return jobs.control(user, organization_id, project_id, program_id, body)

    @app.post(project + "/tasks/{task_id}/jobs", status_code=202)
    def enqueue(organization_id: str, project_id: str, program_id: str, task_id: str, body: JobEnqueue, user: User):
        return jobs.enqueue(user, organization_id, project_id, program_id, task_id, body)

    @app.get(project + "/jobs/{job_id}")
    def read(organization_id: str, project_id: str, program_id: str, job_id: str, user: User):
        return jobs.read(user, organization_id, project_id, program_id, job_id)

    @app.post("/api/work-worker/claim")
    def claim(body: WorkerClaim, credential: Worker):
        return jobs.claim(credential, body.claim_id)

    @app.post(project + "/jobs/{job_id}/cancel", status_code=204)
    def cancel(organization_id: str, project_id: str, program_id: str, job_id: str, user: User):
        jobs.cancel(user, organization_id, project_id, program_id, job_id)

    @app.get(project + "/jobs/{job_id}/effects")
    def effects(organization_id: str, project_id: str, program_id: str, job_id: str, user: User, after: str = "", limit: int = 50):
        return jobs.effects(user, organization_id, project_id, program_id, job_id, after, limit)

    @app.post(project + "/effects/{effect_id}/reconcile")
    def reconcile(organization_id: str, project_id: str, program_id: str, effect_id: str, body: EffectReconciliation, user: User):
        return jobs.reconcile(user, organization_id, project_id, program_id, effect_id, body)

    @app.post(project + "/tasks/{task_id}/reopen")
    def reopen(organization_id: str, project_id: str, program_id: str, task_id: str, body: TaskReopen, user: User):
        return jobs.reopen(user, organization_id, project_id, program_id, task_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/heartbeat")
    def heartbeat(job_id: str, body: JobLease, credential: Worker):
        return jobs.heartbeat(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/context")
    def context(job_id: str, body: JobLease, credential: Worker):
        return jobs.context(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/inputs/read")
    def input_read(job_id: str, body: InputRead, credential: Worker):
        return jobs.input_read(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/compact")
    def compact(job_id: str, body: CompactionNote, credential: Worker):
        return jobs.compact(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/result")
    def complete(job_id: str, body: JobResult, credential: Worker):
        return jobs.complete(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/effects", status_code=201)
    def intent(job_id: str, body: EffectIntent, credential: Worker):
        return jobs.effect_intent(credential, job_id, body)

    @app.post("/api/work-worker/jobs/{job_id}/effects/{effect_id}/receipt")
    def receipt(job_id: str, effect_id: str, body: EffectReceipt, credential: Worker):
        return jobs.effect_receipt(credential, job_id, effect_id, body)
