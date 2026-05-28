"""JMP/JSL export helpers for InsightForge.

The Streamlit app still computes results locally with Python so it can run in a
browser-hosted environment, but these helpers generate JMP Scripting Language
(JSL) that reproduces the same analysis workflow in desktop JMP once the user
opens the cleaned CSV/XLSX data table.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from ask_your_data_engine import AnalysisIntent, AnalysisResult

MAX_JMP_COLUMNS = 40


def _jsl_string(value: Any) -> str:
    """Return a double-quoted JSL string literal."""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _jsl_name(column: str) -> str:
    """Return a robust JMP name literal for a data-table column."""
    text = str(column).replace("\\", "\\\\").replace('"', '\\"')
    return f':"{text}"n'


def _jsl_column_list(columns: Iterable[str]) -> str:
    return ", ".join(_jsl_name(column) for column in columns)


def _safe_columns(df: pd.DataFrame, columns: Iterable[str] | None = None, *, numeric_only: bool = False) -> list[str]:
    """Keep requested columns that exist in df and optionally require numeric dtype."""
    if columns is None:
        candidates = df.columns.astype(str).tolist()
    else:
        candidates = [str(column) for column in columns if str(column) in df.columns.astype(str).tolist()]
    if numeric_only:
        numeric = set(df.select_dtypes(include=np.number).columns.astype(str))
        candidates = [column for column in candidates if column in numeric]
    return candidates[:MAX_JMP_COLUMNS]


def generate_jmp_startup_script(source_filename: str = "cleaned_dataset.csv") -> str:
    """Generate the reusable opening block for downloaded JSL files."""
    return f"""Names Default To Here( 1 );

// InsightForge generated this JMP script for {source_filename}.
// In JMP, run the script and choose the cleaned CSV/XLSX file downloaded from the app.
data_path = Pick File( "Choose the cleaned data file exported from InsightForge", "$DESKTOP", {{"CSV|csv", "Excel|xlsx;xls", "All Files|*"}}, 1, 0 );
If( Is Missing( data_path ), Throw( "No data file selected." ) );
dt = Open( data_path );
dt << Set Name( "InsightForge Analysis Data" );
"""


def generate_jmp_data_quality_script(df: pd.DataFrame) -> str:
    """Create JSL that builds a missingness/profile table in JMP."""
    per_column: list[str] = []
    for column in df.columns.astype(str):
        per_column.extend(
            [
                f"Col = Column( dt, {_jsl_string(column)} );",
                "values = Col << Get Values;",
                "profile_dt << Add Rows( 1 );",
                "profile_row = N Rows( profile_dt );",
                f"Column( profile_dt, \"Column\" )[profile_row] = {_jsl_string(column)};",
                f"Column( profile_dt, \"Python/Pandas Type\" )[profile_row] = {_jsl_string(str(df[column].dtype))};",
                "Column( profile_dt, \"Missing Count\" )[profile_row] = N Rows( dt ) - N Rows( Loc Nonmissing( values ) );",
                "Column( profile_dt, \"Unique Count\" )[profile_row] = N Items( Associative Array( values ) << Get Keys );",
            ]
        )
    return """// Data-quality profile: missing values and unique counts by column.
profile_dt = New Table( "InsightForge Column Profile",
    New Column( "Column", Character ),
    New Column( "Python/Pandas Type", Character ),
    New Column( "Missing Count", Numeric, Continuous ),
    New Column( "Unique Count", Numeric, Continuous )
);
""" + "\n".join(per_column) + "\n"


def generate_jmp_full_workflow_script(df: pd.DataFrame, source_filename: str, cleaning_notes: Iterable[str] | None = None) -> str:
    """Generate a broad JMP workflow matching the main Python app sections."""
    numeric = _safe_columns(df, numeric_only=True)
    categorical = [column for column in _safe_columns(df) if column not in numeric][:MAX_JMP_COLUMNS]
    notes = "\n".join(f"// - {note}" for note in (cleaning_notes or [])) or "// - No cleaning notes were recorded."
    sections = [generate_jmp_startup_script(source_filename), "// Cleaning choices applied in InsightForge before export:\n" + notes]
    sections.append(generate_jmp_data_quality_script(df))
    if numeric:
        sections.append(
            "// Descriptive statistics and histograms for numeric columns.\n"
            f"dt << Distribution( Continuous Distribution( Column( {_jsl_column_list(numeric)} ) ) );"
        )
    if categorical:
        sections.append(
            "// Frequency tables for categorical columns.\n"
            f"dt << Distribution( Nominal Distribution( Column( {_jsl_column_list(categorical)} ) ) );"
        )
    if len(numeric) >= 2:
        sections.append(
            "// Correlations, scatterplot matrix, and multivariate relationships.\n"
            f"dt << Multivariate( Y( {_jsl_column_list(numeric[:12])} ), Pairwise Correlations( 1 ), Scatterplot Matrix( 1 ) );"
        )
    if numeric and categorical:
        sections.append(
            "// Example group comparison: first numeric measure by first categorical grouping column.\n"
            f"dt << Oneway( Y( {_jsl_name(numeric[0])} ), X( {_jsl_name(categorical[0])} ), Means and Std Dev( 1 ), Box Plots( 1 ), Unequal Variances( 1 ) );"
        )
    if len(categorical) >= 2:
        sections.append(
            "// Example contingency analysis for the first two categorical columns.\n"
            f"dt << Contingency( Y( {_jsl_name(categorical[0])} ), X( {_jsl_name(categorical[1])} ), Contingency Table( 1 ), Tests( 1 ) );"
        )
    if len(numeric) >= 2:
        target = numeric[-1]
        predictors = numeric[:-1][:8]
        sections.append(
            "// Example least-squares model using the last numeric column as the response.\n"
            f"dt << Fit Model( Y( {_jsl_name(target)} ), Effects( {_jsl_column_list(predictors)} ), Personality( \"Standard Least Squares\" ), Emphasis( \"Effect Leverage\" ), Run( {_jsl_name(target)} << {{Summary of Fit( 1 ), Analysis of Variance( 1 ), Parameter Estimates( 1 ), Plot Actual by Predicted( 1 ), Plot Residual by Predicted( 1 )}} ) );"
        )
    sections.append("// Save this script inside the JMP data table if you want a reusable JMP workflow.\n")
    return "\n\n".join(sections)


def generate_jmp_script_for_analysis(
    intent: AnalysisIntent | None,
    result: AnalysisResult | None,
    df: pd.DataFrame,
    source_filename: str = "cleaned_dataset.csv",
) -> str:
    """Generate focused JSL for a single Ask Your Data analysis result."""
    intent_name = (intent.intent if intent else result.intent if result else "summary") or "summary"
    selected = (result.selected_columns if result else {}) or {}
    columns = [str(column) for column in selected.get("columns", []) if str(column) in df.columns.astype(str).tolist()]
    target = selected.get("target") or selected.get("numeric") or selected.get("categorical")
    group = selected.get("group")
    predictors = [str(column) for column in selected.get("predictors", []) if str(column) in df.columns.astype(str).tolist()]
    numeric = _safe_columns(df, columns or None, numeric_only=True)
    all_numeric = _safe_columns(df, numeric_only=True)
    all_categorical = [column for column in _safe_columns(df) if column not in all_numeric]

    body: list[str] = []
    if intent_name in {"summary", "descriptive_statistics"}:
        if all_numeric:
            body.append(f"dt << Distribution( Continuous Distribution( Column( {_jsl_column_list(all_numeric[:20])} ) ) );")
        if all_categorical:
            body.append(f"dt << Distribution( Nominal Distribution( Column( {_jsl_column_list(all_categorical[:20])} ) ) );")
    elif intent_name in {"correlation", "correlation_heatmap", "scatter_matrix"}:
        corr_cols = numeric if len(numeric) >= 2 else all_numeric[:12]
        body.append(f"dt << Multivariate( Y( {_jsl_column_list(corr_cols)} ), Pairwise Correlations( 1 ), Scatterplot Matrix( 1 ) );" if len(corr_cols) >= 2 else "// Need at least two numeric columns for JMP Multivariate correlations.")
    elif intent_name in {"scatter_plot", "regression_plot"} and len(columns) >= 2:
        body.append(f"dt << Bivariate( Y( {_jsl_name(columns[1])} ), X( {_jsl_name(columns[0])} ), Fit Line( 1 ) );")
    elif intent_name in {"histogram", "kde_plot", "outlier_detection"}:
        col = (columns or numeric or all_numeric[:1] or [None])[0]
        body.append(f"dt << Distribution( Continuous Distribution( Column( {_jsl_name(col)} ), Histogram( 1 ), Outlier Box Plot( 1 ) ) );" if col else "// Need a numeric column for this JMP distribution.")
    elif intent_name in {"boxplot", "violin_plot", "anova", "t_test", "mann_whitney", "kruskal_wallis"}:
        y = target if target in df.columns.astype(str).tolist() else (numeric[0] if numeric else (all_numeric[0] if all_numeric else None))
        x = group if group in df.columns.astype(str).tolist() else (columns[1] if len(columns) > 1 else (all_categorical[0] if all_categorical else None))
        body.append(f"dt << Oneway( Y( {_jsl_name(y)} ), X( {_jsl_name(x)} ), Means and Std Dev( 1 ), Box Plots( 1 ), Unequal Variances( 1 ), Wilcoxon Test( 1 ) );" if y and x else "// Need one numeric response and one grouping column for JMP Oneway.")
    elif intent_name in {"bar_chart", "count_plot", "pie_chart"}:
        x = columns[0] if columns else (all_categorical[0] if all_categorical else None)
        body.append(f"dt << Distribution( Nominal Distribution( Column( {_jsl_name(x)} ) ) );" if x else "// Need a categorical column for this JMP frequency analysis.")
    elif intent_name in {"cross_tabulation", "chi_square_test", "stacked_bar_chart", "grouped_bar_chart"}:
        pair = columns if len(columns) >= 2 else all_categorical[:2]
        body.append(f"dt << Contingency( Y( {_jsl_name(pair[0])} ), X( {_jsl_name(pair[1])} ), Contingency Table( 1 ), Tests( 1 ), Mosaic Plot( 1 ) );" if len(pair) >= 2 else "// Need two categorical columns for JMP Contingency.")
    elif intent_name in {"linear_regression", "logistic_regression", "random_forest", "decision_tree", "feature_importance"}:
        y = target if target in df.columns.astype(str).tolist() else (all_numeric[-1] if all_numeric else None)
        xs = predictors or [column for column in all_numeric if column != y][:8]
        if y and xs and intent_name == "logistic_regression":
            body.append(f"dt << Fit Model( Y( {_jsl_name(y)} ), Effects( {_jsl_column_list(xs)} ), Personality( \"Nominal Logistic\" ), Run );")
        elif y and xs and intent_name in {"random_forest", "feature_importance"}:
            body.append(f"dt << Bootstrap Forest( Y( {_jsl_name(y)} ), X( {_jsl_column_list(xs)} ), Method( \"Bootstrap Forest\" ) );")
        elif y and xs and intent_name == "decision_tree":
            body.append(f"dt << Partition( Y( {_jsl_name(y)} ), X( {_jsl_column_list(xs)} ) );")
        elif y and xs:
            body.append(f"dt << Fit Model( Y( {_jsl_name(y)} ), Effects( {_jsl_column_list(xs)} ), Personality( \"Standard Least Squares\" ), Emphasis( \"Effect Leverage\" ), Run );")
        else:
            body.append("// Need a target and at least one predictor for this JMP model.")
    elif intent_name in {"missing_values", "data_quality"}:
        body.append(generate_jmp_data_quality_script(df))
    else:
        body.append("// No exact JMP platform mapping was detected; use the full workflow script for broad profiling.")

    title = result.title if result else intent_name.replace("_", " ").title()
    header = generate_jmp_startup_script(source_filename)
    return header + "\n// Focused Ask Your Data JMP/JSL script: " + title.replace("\n", " ") + "\n" + "\n".join(body) + "\n"
