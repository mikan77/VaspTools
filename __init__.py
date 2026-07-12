"""Workflow helpers for VASP mechanical-property calculations."""

from __future__ import annotations

from typing import Any


__all__ = [
    "Calculation",
    "CalculationRunner",
    "ClusterOccurrence",
    "ClusterType",
    "CrystalNMerGenerator",
    "ElasticFitResult",
    "ElasticMode",
    "EOSFitResult",
    "EOSMode",
    "EOSPoint",
    "IncarPolicy",
    "MechanicalPipeline",
    "GenerationResult",
    "MoleculeInstance",
    "MoleculeType",
    "MolecularStructureExtractor",
    "MoleculeExtractionConfig",
    "GeometryType",
    "ExtractionResult",
    "MoleculeOccurrence",
    "SymmetryMapping",
    "SymmetryUniqueMolecule",
    "NMerConfig",
    "OrcaConfig",
    "PipelineConfig",
    "PipelineInputs",
    "ParamScanMode",
    "SbatchRunner",
    "StrainStressPoint",
    "Submission",
    "VaspCalculationFactory",
    "WorkflowMode",
    "apply_strain",
    "birch_murnaghan_energy",
    "check_mechanical_stability",
    "discover_calculations",
    "fit_elastic_tensor",
    "fit_eos",
    "generate_strain_vectors",
    "make_stage_incar",
    "mechanical_properties",
    "parse_sbatch_job_id",
    "render_job_script",
    "render_two_stage_job_script",
    "resolve_job_template_path",
    "scale_structure_to_volume",
    "strain_matrix_from_voigt",
    "submit_sbatch",
    "validate_kspacing_incar",
]


def __getattr__(name: str) -> Any:
    """Lazily import public objects."""

    if name in {"Calculation", "PipelineConfig", "PipelineInputs"}:
        from .core.models import Calculation, PipelineConfig, PipelineInputs

        exports = {
            "Calculation": Calculation,
            "PipelineConfig": PipelineConfig,
            "PipelineInputs": PipelineInputs,
        }
    elif name == "MechanicalPipeline":
        from .core.pipeline import MechanicalPipeline

        exports = {"MechanicalPipeline": MechanicalPipeline}
    elif name in {
        "ClusterOccurrence",
        "ClusterType",
        "CrystalNMerGenerator",
        "GenerationResult",
        "MoleculeInstance",
        "MoleculeType",
        "NMerConfig",
        "OrcaConfig",
    }:
        from .clusters import (
            ClusterOccurrence,
            ClusterType,
            CrystalNMerGenerator,
            GenerationResult,
            MoleculeInstance,
            MoleculeType,
            NMerConfig,
            OrcaConfig,
        )

        exports = {
            "ClusterOccurrence": ClusterOccurrence,
            "ClusterType": ClusterType,
            "CrystalNMerGenerator": CrystalNMerGenerator,
            "GenerationResult": GenerationResult,
            "MoleculeInstance": MoleculeInstance,
            "MoleculeType": MoleculeType,
            "NMerConfig": NMerConfig,
            "OrcaConfig": OrcaConfig,
        }
    elif name in {
        "ExtractionResult",
        "GeometryType",
        "MolecularStructureExtractor",
        "MoleculeExtractionConfig",
        "MoleculeOccurrence",
        "SymmetryMapping",
        "SymmetryUniqueMolecule",
    }:
        from .molecules import (
            ExtractionResult,
            GeometryType,
            MolecularStructureExtractor,
            MoleculeExtractionConfig,
            MoleculeOccurrence,
            SymmetryMapping,
            SymmetryUniqueMolecule,
        )

        exports = {
            "ExtractionResult": ExtractionResult,
            "GeometryType": GeometryType,
            "MolecularStructureExtractor": MolecularStructureExtractor,
            "MoleculeExtractionConfig": MoleculeExtractionConfig,
            "MoleculeOccurrence": MoleculeOccurrence,
            "SymmetryMapping": SymmetryMapping,
            "SymmetryUniqueMolecule": SymmetryUniqueMolecule,
        }
    elif name in {"EOSMode", "ElasticMode", "WorkflowMode"}:
        from .workflows import ElasticMode, EOSMode, WorkflowMode

        exports = {
            "ElasticMode": ElasticMode,
            "EOSMode": EOSMode,
            "WorkflowMode": WorkflowMode,
        }
    elif name == "ParamScanMode":
        from .workflows import ParamScanMode

        exports = {"ParamScanMode": ParamScanMode}
    elif name == "VaspCalculationFactory":
        from .core.factory import VaspCalculationFactory

        exports = {"VaspCalculationFactory": VaspCalculationFactory}
    elif name == "IncarPolicy":
        from .core.policies import IncarPolicy

        exports = {"IncarPolicy": IncarPolicy}
    elif name in {"CalculationRunner", "SbatchRunner"}:
        from .execution.runners import CalculationRunner, SbatchRunner

        exports = {"CalculationRunner": CalculationRunner, "SbatchRunner": SbatchRunner}
    elif name in {
        "Submission",
        "parse_sbatch_job_id",
        "render_job_script",
        "render_two_stage_job_script",
        "resolve_job_template_path",
        "submit_sbatch",
    }:
        from .io.jobs import (
            Submission,
            parse_sbatch_job_id,
            render_job_script,
            render_two_stage_job_script,
            resolve_job_template_path,
            submit_sbatch,
        )

        exports = {
            "Submission": Submission,
            "parse_sbatch_job_id": parse_sbatch_job_id,
            "render_job_script": render_job_script,
            "render_two_stage_job_script": render_two_stage_job_script,
            "resolve_job_template_path": resolve_job_template_path,
            "submit_sbatch": submit_sbatch,
        }
    elif name in {"make_stage_incar", "validate_kspacing_incar"}:
        from .io.incar import make_stage_incar, validate_kspacing_incar

        exports = {
            "make_stage_incar": make_stage_incar,
            "validate_kspacing_incar": validate_kspacing_incar,
        }
    elif name == "discover_calculations":
        from .io.discovery import discover_calculations

        exports = {"discover_calculations": discover_calculations}
    elif name in {
        "apply_strain",
        "generate_strain_vectors",
        "scale_structure_to_volume",
        "strain_matrix_from_voigt",
    }:
        from .structures import (
            apply_strain,
            generate_strain_vectors,
            scale_structure_to_volume,
            strain_matrix_from_voigt,
        )

        exports = {
            "apply_strain": apply_strain,
            "generate_strain_vectors": generate_strain_vectors,
            "scale_structure_to_volume": scale_structure_to_volume,
            "strain_matrix_from_voigt": strain_matrix_from_voigt,
        }
    elif name in {"EOSFitResult", "EOSPoint", "birch_murnaghan_energy", "fit_eos"}:
        from .analysis.eos import EOSFitResult, EOSPoint, birch_murnaghan_energy, fit_eos

        exports = {
            "EOSFitResult": EOSFitResult,
            "EOSPoint": EOSPoint,
            "birch_murnaghan_energy": birch_murnaghan_energy,
            "fit_eos": fit_eos,
        }
    elif name in {
        "ElasticFitResult",
        "StrainStressPoint",
        "check_mechanical_stability",
        "fit_elastic_tensor",
        "mechanical_properties",
    }:
        from .analysis.elastic import (
            ElasticFitResult,
            StrainStressPoint,
            check_mechanical_stability,
            fit_elastic_tensor,
            mechanical_properties,
        )

        exports = {
            "ElasticFitResult": ElasticFitResult,
            "StrainStressPoint": StrainStressPoint,
            "check_mechanical_stability": check_mechanical_stability,
            "fit_elastic_tensor": fit_elastic_tensor,
            "mechanical_properties": mechanical_properties,
        }
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
