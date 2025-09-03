from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app import models, schemas, auth
from app.database import get_db
from app.utils.email import send_verification_email
from jose import JWTError, jwt
from datetime import timedelta
import os

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/signup", response_model=schemas.UserOut)
async def signup(user: schemas.UserCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == str.lower(user.email)).first()
    if existing:
        # If exists but not verified → send them another verification email
        if not existing.is_verified:
            verification_token = auth.create_access_token(
                data={"sub": user.email},
                expires_delta=timedelta(hours=1)
            )
            background_tasks.add_task(send_verification_email, user.email, verification_token)
            raise HTTPException(status_code=400, detail="Email already registered but not verified. Verification email resent.")

        # If exists and verified
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = auth.get_password_hash(user.password)
    new_user = models.User(username=user.username, email=str.lower(user.email), hashed_password=hashed)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Generate email verification token
    verification_token = auth.create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(hours=1)
    )

    background_tasks.add_task(send_verification_email, user.email, verification_token)

    return new_user

@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=400, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_verified:
        return {"message": "Email already verified"}

    user.is_verified = True
    db.commit()
    return {"message": "Email verified successfully"}


@router.post("/login")
def login(form: schemas.UserLogin, response: Response, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form.email).first()
    if not user or not auth.verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Verify your email first")

    token = auth.create_access_token(data={"sub": user.email})

    # Set cookie with JWT token
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,             # Set True in production (HTTPS)
        samesite="None",         # Required for cross-site cookies
        max_age=60 * 60 * 24     # 1 day
    )

    return {"message": "Login successful","access_token": token, "token_type": "bearer"}