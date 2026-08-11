"""Workbook I/O: turning Scenario objects into the data template, the scenario space metadata and the coverage
report, and reading them back."""
from .workbooks import (read_space_metadata, read_scenarios, write_data_template,
                        write_coverage_report, write_space_metadata, write_scenario_graph)

__all__ = ["write_scenario_graph", "read_scenarios", "write_data_template",
          "write_coverage_report", "write_space_metadata", "read_space_metadata"]
