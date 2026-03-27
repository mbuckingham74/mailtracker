from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..services.analytics import build_analytics_context, export_analytics_csv
from ..services.dashboard import (
    build_dashboard_context,
    build_detail_context,
    delete_track,
    export_tracks_csv,
    toggle_track_pin,
    update_track_notes,
)
from ..services.recipients import build_recipient_detail_context, build_recipients_context
from ..web import templates

router = APIRouter()


def is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated", False)


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == settings.dashboard_username and password == settings.dashboard_password:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return redirect_to_login()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    filter: str = "all",
    search: str = "",
    date_range: str = "all",
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    if not is_authenticated(request):
        return redirect_to_login()

    context = await build_dashboard_context(
        db,
        filter_value=filter,
        search=search,
        date_range=date_range,
        page=page,
    )
    return templates.TemplateResponse("dashboard.html", {"request": request, **context})


@router.get("/detail/{track_id}", response_class=HTMLResponse)
async def detail_page(request: Request, track_id: str, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return redirect_to_login()

    context = await build_detail_context(db, track_id)
    return templates.TemplateResponse("detail.html", {"request": request, **context})


@router.post("/delete/{track_id}")
async def delete_track_route(request: Request, track_id: str, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return redirect_to_login()

    await delete_track(db, track_id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/pin/{track_id}")
async def toggle_pin(request: Request, track_id: str, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return redirect_to_login()

    await toggle_track_pin(db, track_id)
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@router.post("/notes/{track_id}")
async def update_notes(
    request: Request,
    track_id: str,
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if not is_authenticated(request):
        return redirect_to_login()

    await update_track_notes(db, track_id, notes)
    return RedirectResponse(url=f"/detail/{track_id}", status_code=303)


@router.get("/export")
async def export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return redirect_to_login()

    filename, csv_content = await export_tracks_csv(db)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analytics/export")
async def export_analytics(
    request: Request,
    date_range: str = "30",
    db: AsyncSession = Depends(get_db),
):
    if not is_authenticated(request):
        return redirect_to_login()

    filename, csv_content = await export_analytics_csv(db, date_range)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(
    request: Request,
    date_range: str = "30",
    db: AsyncSession = Depends(get_db),
):
    if not is_authenticated(request):
        return redirect_to_login()

    context = await build_analytics_context(db, date_range)
    return templates.TemplateResponse("analytics.html", {"request": request, **context})


@router.get("/recipients", response_class=HTMLResponse)
async def recipients_list(
    request: Request,
    search: str = "",
    sort: str = "score",
    order: str = "desc",
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    if not is_authenticated(request):
        return redirect_to_login()

    context = await build_recipients_context(
        db,
        search=search,
        sort=sort,
        order=order,
        page=page,
    )
    return templates.TemplateResponse("recipients.html", {"request": request, **context})


@router.get("/recipients/{email:path}", response_class=HTMLResponse)
async def recipient_detail(
    request: Request,
    email: str,
    db: AsyncSession = Depends(get_db),
):
    if not is_authenticated(request):
        return redirect_to_login()

    context = await build_recipient_detail_context(db, email)
    return templates.TemplateResponse("recipient_detail.html", {"request": request, **context})
