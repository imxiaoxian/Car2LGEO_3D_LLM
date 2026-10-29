"""Explicit SUV styling prior, conditioned on the reconstructed roof profile."""
import re

import numpy as np

from .coupe import fit_coupe


def styling(spec):
    identity = " ".join(str(spec.get(key, "")) for key in ("model", "variant")).lower() if spec else ""
    return "lotus-for-me" if "lotus" in identity and re.search(r"\bfor\s*me\b", identity) else None


def fit_suv(fit, spec):
    fit_coupe(fit, spec)
    length, width = fit["length_studs"], fit["width_studs"]
    start = int(np.clip(fit["roof_start_stud"], round(length*.32), round(length*.40)))
    end = int(np.clip(fit["roof_end_stud"], start+8, length-6))
    ratio = fit.get("source_upper_body_width_ratio")
    choices = [size for size in (6, 8) if size <= width-2]
    cabin_width = min(choices, key=lambda size: abs(size-width*ratio)) if ratio is not None else choices[-1]
    glass_height = 9 if cabin_width == 8 else 6
    fit.update(recipe="suv-v1", styling_prior=styling(spec), roof_start_stud=start, roof_end_stud=end,
               cabin_width_studs=cabin_width,
               cabin_width_source="mesh upper body quantized to native windscreen" if ratio is not None else "native fallback",
               windscreen_part="20684" if cabin_width == 8 else "92583",
               windscreen_height_plates=glass_height,
               deck_top_plates=fit["roof_top_plates"]-glass_height-2,
               fitted_windscreen_angle_degrees=56.3 if cabin_width == 8 else 45.0,
               design_prior="For Me SUV; mesh roof interval bounded by explicit four-door cabin prior; native glazing",
               fixed_parameters=["four door skins", "native glazing and window size", "symmetric body"],
               layout={"bonnet_interval_studs": [0, start-4], "front_glass_interval_studs": [start-3, start-1],
                       "roof_interval_studs": [start-1, end+1], "rear_glass_interval_studs": [end+1, end+3],
                       "tailgate_interval_studs": [end+4, length-1], "door_count": 4,
                       "engine_position": "front", "rear_wing_default": False})
    fit["limitations"].append("SUV roof bounds, four doors and Lotus fascia are explicit styling priors, not recovered CAD.")
    return fit
