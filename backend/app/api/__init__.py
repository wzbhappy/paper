from fastapi import APIRouter

from app.api import directions, graph, jobs, papers, projects, reviews, search

router = APIRouter()
router.include_router(projects.router, prefix="/projects", tags=["projects"])
router.include_router(
    papers.router, prefix="/projects/{project_id}/papers", tags=["papers"]
)
router.include_router(
    search.router, prefix="/projects/{project_id}/search", tags=["search"]
)
router.include_router(
    directions.router, prefix="/projects/{project_id}/directions", tags=["directions"]
)
router.include_router(
    reviews.router, prefix="/projects/{project_id}/review", tags=["review"]
)
router.include_router(
    graph.router, prefix="/projects/{project_id}/graph", tags=["graph"]
)
router.include_router(jobs.router, prefix="/projects/{project_id}/jobs", tags=["jobs"])
