from pydantic import BaseModel, Field


class SimulationConfig(BaseModel):
    seconds_per_step: int = Field(default=15, ge=1, le=300)
    max_steps: int = Field(default=100, ge=1, le=10_000)
    max_correction_attempts: int = Field(default=3, ge=0, le=10)
    detection_radius_m: float = Field(default=450.0, gt=0)
    dynamic_change_interval: int = Field(default=6, ge=0)
