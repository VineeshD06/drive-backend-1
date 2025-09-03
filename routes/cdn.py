import os, uuid, io
from fastapi import UploadFile, File, Depends, APIRouter, HTTPException, Form, Body
from sqlalchemy.orm import Session
from PIL import Image
import pillow_heif
from app import models
from app.database import get_db
from app.auth import get_current_user
from app.utils.s3 import upload_to_s3, delete_from_s3, rename_in_s3
import uuid
from uuid import UUID

router = APIRouter()



#helper functions
def normalize_folder_path(path: str) -> str:
    """Always return a normalized folder path starting and ending with '/'."""
    path = path.strip("/")  # remove leading/trailing slashes
    return f"/{path}/" if path else "/"

def normalize_file_path(path: str) -> str:
    """Normalize file's drive_path to always end with '/' (represents parent folder)."""
    return normalize_folder_path(path)


# Absolute path for store folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "store")
os.makedirs(STORE_DIR, exist_ok=True)

@router.post("/fileSave")
async def fileSave(
    file: UploadFile = File(...),
    drive_path: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Ensure user-specific folder in local storage
    user_folder = os.path.join(STORE_DIR, str(user.id))
    os.makedirs(user_folder, exist_ok=True)

    # Ensure drive_path is normalized (no leading/trailing slashes)
    drive_path = drive_path.strip("/")

    # --- Ensure parent folders exist virtually ---
    if drive_path:
        parts = drive_path.split("/")
        current_path = ""
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part

            existing_folder = (
                db.query(models.Folder)
                .filter(models.Folder.owner_id == user.id, models.Folder.drive_path == normalize_folder_path(current_path))
                .first()
            )

            if not existing_folder:
                new_folder = models.Folder(
                    name=part,
                    drive_path=normalize_folder_path(current_path),
                    owner_id=user.id,
                )
                db.add(new_folder)
                db.commit()
                db.refresh(new_folder)

    # Original filename
    original_filename = file.filename
    name, ext = os.path.splitext(original_filename)
    file_type = file.content_type

    # --- Check duplicate filename in same folder ---
    existing_file = (
        db.query(models.File)
        .filter(
            models.File.owner_id == user.id,
            models.File.drive_path == normalize_folder_path(drive_path),
            models.File.original_name == original_filename,
        )
        .first()
    )
    if existing_file:
        raise HTTPException(status_code=400, detail="File with same name already exists in this folder.")

    # Generate unique filename
    unique_filename = f"{name}_{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(user_folder, unique_filename)

    # Read file into memory once
    file_bytes = await file.read()

    # Handle HEIC → JPG conversion
    if file_type in ["image/heic", "image/heif"]:
        heif_file = pillow_heif.read_heif(io.BytesIO(file_bytes))
        image = Image.frombytes(
            heif_file.mode, heif_file.size, heif_file.data,
            "raw", heif_file.mode, heif_file.stride,
        )
        unique_filename = f"{name}_{uuid.uuid4().hex}.jpg"
        file_path = os.path.join(user_folder, unique_filename)
        image.save(file_path, "JPEG")
        file_type = "image/jpeg"
    else:
        # Normal file save
        with open(file_path, "wb") as f:
            f.write(file_bytes)

    # Upload to S3
    s3_key = f"{user.id}/{unique_filename}"
    s3_url = upload_to_s3(file_path, file_type, s3_key)

    # Save record in DB
    new_file = models.File(
        original_name=original_filename,
        stored_name=unique_filename,
        physical_path=file_path,
        drive_path = normalize_file_path(drive_path),
        content_type=file_type,
        s3_path=s3_key,
        s3_url=s3_url,
        owner_id=user.id
    )
    db.add(new_file)
    db.commit()
    db.refresh(new_file)

    return {
        "message": "File saved successfully",
        "file_id": new_file.id,
        "original_filename": original_filename,
        "stored_filename": unique_filename,
        "drive_path": normalize_folder_path(drive_path),
        "s3_url": s3_url,
        "local_path": file_path
    }


@router.get("/my-files")
def get_user_files(user=Depends(get_current_user), db: Session = Depends(get_db)):
    files = db.query(models.File).filter(models.File.owner_id == user.id).all()
    folders = db.query(models.Folder).filter(models.Folder.owner_id == user.id).all()

    return {"files":[
        {
            "id": str(f.id),
            "original_name": f.original_name,
            "stored_name": f.stored_name,
            "drive_path": f.drive_path or "",
            "s3_url": f.s3_url,
            "content_type": f.content_type,
        }
        for f in files
    ],"folder":[
        {
            "id": str(f.id),
            "name": f.name,
            "drive_path": f.drive_path or "",
        }
        for f in folders
    ]
    }


@router.put("/rename-file/{file_id}")
def rename_file(file_id: UUID, new_file_name: str = Body(..., embed=True), user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Fetch file entry from DB
    file_entry = db.query(models.File).filter(models.File.id == file_id, models.File.owner_id == user.id).first()
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    # Extract file extension
    name, ext = os.path.splitext(new_file_name)
    if not ext:
        _, old_ext = os.path.splitext(file_entry.original_name)
        ext = old_ext  # preserve old extension if not given

    # Generate unique filename
    unique_filename = f"{name}_{uuid.uuid4().hex}{ext}"

    # --- Local rename (if file is stored locally) ---
    old_path = file_entry.physical_path
    new_path = os.path.join(os.path.dirname(old_path), unique_filename)

    try:
        if os.path.exists(old_path):
            os.rename(old_path, new_path)
        else:
            print(f"Local file not found: {old_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error renaming local file: {str(e)}")

    # --- S3 rename (actually copy+delete) ---
    old_s3_key = file_entry.s3_path
    new_s3_key = f"{user.id}/{unique_filename}"
    new_s3_url=f"s3://takneek-bucket/{user.id}/{unique_filename}"

    rename_in_s3(old_s3_key,new_s3_key)

    # --- Update DB ---
    file_entry.original_name = new_file_name
    file_entry.stored_name = unique_filename
    file_entry.physical_path = new_path
    file_entry.s3_url=new_s3_url
    file_entry.s3_path = new_s3_key

    db.commit()
    db.refresh(file_entry)

    return {"message": "File renamed successfully", "file": file_entry}

@router.put("/rename-folder")
def rename_folder(
    old_folder_path: str = Body(..., embed=True),
    new_folder_name: str = Body(..., embed=True),
    user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Rename a virtual folder by updating drive_path in both Folder and File tables.
    No changes happen in local storage or S3.
    """

    # Ensure consistency: all folder paths end with a slash
    old_folder_path = normalize_folder_path(old_folder_path)


    # ---- Find the folder being renamed ----
    folder = db.query(models.Folder).filter(
        models.Folder.owner_id == user.id,
        models.Folder.drive_path == old_folder_path
    ).first()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # ---- Build new folder path ----
    parent_path = "/".join(old_folder_path.strip("/").split("/")[:-1])
    if parent_path:
        parent_path += "/"

    new_folder_path = normalize_folder_path(f"{parent_path}{new_folder_name}")

    # ---- Update the folder itself ----
    folder.drive_path = new_folder_path
    folder.name = new_folder_name  # assuming Folder table has `name`

    # ---- Update all subfolders ----
    subfolders = db.query(models.Folder).filter(
        models.Folder.owner_id == user.id,
        models.Folder.drive_path.startswith(old_folder_path),
        models.Folder.drive_path != old_folder_path
    ).all()

    for sf in subfolders:
        sf.drive_path = sf.drive_path.replace(old_folder_path, new_folder_path, 1)

    # ---- Update all files inside this folder ----
    files = db.query(models.File).filter(
        models.File.owner_id == user.id,
        models.File.drive_path.startswith(old_folder_path)
    ).all()

    for f in files:
        f.drive_path = f.drive_path.replace(old_folder_path, new_folder_path, 1)

    db.commit()

    return {
        "message": "Folder renamed successfully",
        "new_folder_path": new_folder_path
    }

@router.delete("/delete-file/{file_id}")
def delete_file(file_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    file = db.query(models.File).filter(models.File.id == file_id, models.File.owner_id == user.id).first()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete from local storage
    local_path = os.path.join(STORE_DIR, str(user.id), file.stored_name)
    if os.path.exists(local_path):
        os.remove(local_path)

    # Delete from S3
    if file.s3_path:
        try:
            delete_from_s3(file.s3_path)
        except Exception as e:
            print("S3 delete failed:", e)

    # Delete from DB
    db.delete(file)
    db.commit()

    return {"message": "File deleted successfully"}


@router.post("/create-folder")
def create_folder(
    folder_name: str = Body(..., embed=True),
    parent_path: str = Body(..., embed=True),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Split parent path into parts
    parent_path = parent_path.strip("/")
    print("parent_path is",parent_path)
    parts = parent_path.split("/") if parent_path else []

    current_path = "/"
    created_folders = []
    part_being_checked_parent_path="/"

    for idx, part_being_checked in enumerate(parts):

        existing = db.query(models.Folder).filter(
            models.Folder.owner_id == user.id,
            models.Folder.name == part_being_checked
        ).first()

        if not existing:
            folder = models.Folder(
                name=part_being_checked,
                drive_path=part_being_checked_parent_path,
                owner_id=user.id
            )
            db.add(folder)
            db.commit()
            db.refresh(folder)
            created_folders.append({"id": str(folder.id), "drive_path": folder.drive_path})

        part_being_checked_parent_path=normalize_folder_path(part_being_checked_parent_path+part_being_checked)
        current_path=normalize_folder_path(current_path+part_being_checked)

    # Final folder
    if current_path == "/":
        final_path = f"/"
    else:
        final_path = current_path

    existing = db.query(models.Folder).filter(
        models.Folder.owner_id == user.id,
        models.Folder.drive_path == final_path,
        models.Folder.name == folder_name
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Folder already exists")

    folder = models.Folder(
        name=folder_name,
        drive_path=final_path,
        owner_id=user.id
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    created_folders.append({"id": str(folder.id), "drive_path": folder.drive_path})

    return {
        "message": "Folder created successfully",
        "created_folders": created_folders
    }

@router.delete("/delete-folder")
def delete_folder(
    folder_name: str = Body(..., embed=True),
    parent_path: str = Body(..., embed=True),
    user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Deletes a folder, its subfolders, and all files inside.
    """
    parent_path = normalize_folder_path(parent_path)

    # Check folder exists
    folder = db.query(models.Folder).filter(
        models.Folder.owner_id == user.id,
        models.Folder.drive_path == parent_path,
        models.Folder.name == folder_name
    ).first()

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # --- Delete all subfolders (including this one) ---
    subfolders = db.query(models.Folder).filter(
        models.Folder.owner_id == user.id,
        models.Folder.drive_path.startswith(parent_path)
    ).all()

    for sub in subfolders:
        db.delete(sub)

    # --- Delete all files inside folder and subfolders ---
    files = db.query(models.File).filter(
        models.File.owner_id == user.id,
        models.File.drive_path.startswith(parent_path)
    ).all()

    for f in files:
        # Delete from local storage
        if os.path.exists(f.physical_path):
            os.remove(f.physical_path)

        # Delete from S3
        if f.s3_path:
            delete_from_s3(f.s3_path)

        db.delete(f)

    db.commit()
    return {"message": f"Folder '{parent_path}' and its contents deleted successfully"}
