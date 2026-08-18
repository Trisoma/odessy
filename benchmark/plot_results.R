suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
input_path <- if (length(args) >= 1) args[[1]] else file.path("benchmark", "results")
output_dir <- if (length(args) >= 2) args[[2]] else file.path(input_path, "plots")

resolve_results_dir <- function(path) {
  if (file.exists(file.path(path, "samples.csv"))) {
    return(path)
  }
  if (file.exists(path) && grepl("\\.csv$", path, ignore.case = TRUE)) {
    return(dirname(path))
  }
  stop("Could not find samples.csv in: ", path)
}

results_dir <- resolve_results_dir(input_path)
samples_path <- file.path(results_dir, "samples.csv")
metadata_path <- file.path(results_dir, "metadata.json")

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

samples <- read_csv(
  samples_path,
  show_col_types = FALSE,
  col_types = cols(
    scenario = col_character(),
    iteration = col_integer(),
    is_warmup = col_logical(),
    status = col_character(),
    total_ms = col_double(),
    rss_before_bytes = col_double(),
    rss_after_bytes = col_double(),
    swap_before_bytes = col_double(),
    swap_after_bytes = col_double(),
    record_ms = col_double(),
    transcribe_ms = col_double(),
    proposal_ms = col_double(),
    response_ms = col_double(),
    agent_ms = col_double(),
    synthesize_ms = col_double(),
    play_ms = col_double(),
    started_at = col_character(),
    finished_at = col_character(),
    notes = col_character(),
    error = col_character()
  )
)

metadata <- NULL
if (file.exists(metadata_path)) {
  metadata <- fromJSON(metadata_path, simplifyVector = TRUE)
}

measured <- samples %>% filter(!is_warmup)

stage_cols <- intersect(
  c("record_ms", "transcribe_ms", "proposal_ms", "response_ms", "agent_ms", "synthesize_ms", "play_ms"),
  names(measured)
)

memory_cols <- intersect(
  c("rss_before_bytes", "rss_after_bytes", "swap_before_bytes", "swap_after_bytes"),
  names(measured)
)

memory_to_mb <- function(x) {
  x / 1024 / 1024
}

summary_tbl <- measured %>%
  group_by(scenario) %>%
  summarise(
    runs = sum(status == "ok", na.rm = TRUE),
    failed = sum(status != "ok", na.rm = TRUE),
    mean_ms = mean(total_ms[status == "ok"], na.rm = TRUE),
    median_ms = median(total_ms[status == "ok"], na.rm = TRUE),
    p95_ms = quantile(total_ms[status == "ok"], 0.95, na.rm = TRUE, type = 7),
    max_ms = max(total_ms[status == "ok"], na.rm = TRUE),
    .groups = "drop"
  )

write_csv(summary_tbl, file.path(output_dir, "scenario_summary.csv"))

latency_plot <- ggplot(measured, aes(x = scenario, y = total_ms, fill = scenario)) +
  geom_boxplot(outlier.alpha = 0.15, width = 0.6) +
  geom_jitter(aes(color = scenario), width = 0.14, size = 1.8, alpha = 0.7, show.legend = FALSE) +
  labs(
    title = "Odessy Benchmark Latency",
    subtitle = if (!is.null(metadata) && !is.null(metadata$created_at)) paste("Run:", metadata$created_at) else NULL,
    x = NULL,
    y = "Total time (ms)"
  ) +
  theme_minimal(base_size = 12) +
  theme(legend.position = "none")

ggsave(
  filename = file.path(output_dir, "latency_by_scenario.png"),
  plot = latency_plot,
  width = 10,
  height = 6,
  dpi = 160
)

if (length(memory_cols) > 0) {
  memory_long <- measured %>%
    select(scenario, iteration, all_of(memory_cols)) %>%
    pivot_longer(
      cols = all_of(memory_cols),
      names_to = "metric",
      values_to = "bytes"
    ) %>%
    mutate(
      metric = recode(
        metric,
        rss_before_bytes = "RSS before",
        rss_after_bytes = "RSS after",
        swap_before_bytes = "Swap before",
        swap_after_bytes = "Swap after"
      ),
      value_mb = memory_to_mb(bytes)
    ) %>%
    filter(!is.na(value_mb))

  memory_plot <- ggplot(memory_long, aes(x = iteration, y = value_mb, color = metric, group = metric)) +
    geom_line(linewidth = 0.7) +
    geom_point(size = 1.8) +
    facet_wrap(~scenario, scales = "free_y") +
    labs(
      title = "Odessy Benchmark Memory Pressure",
      x = "Iteration",
      y = "Memory (MB)",
      color = NULL
    ) +
    theme_minimal(base_size = 12)

  ggsave(
    filename = file.path(output_dir, "memory_by_scenario.png"),
    plot = memory_plot,
    width = 12,
    height = 7,
    dpi = 160
  )
}

if (length(stage_cols) > 0) {
  stage_long <- measured %>%
    select(scenario, iteration, all_of(stage_cols)) %>%
    pivot_longer(
      cols = all_of(stage_cols),
      names_to = "stage",
      values_to = "stage_ms"
    ) %>%
    filter(!is.na(stage_ms)) %>%
    group_by(scenario, stage) %>%
    summarise(
      median_ms = median(stage_ms, na.rm = TRUE),
      mean_ms = mean(stage_ms, na.rm = TRUE),
      .groups = "drop"
    )

  write_csv(stage_long, file.path(output_dir, "stage_summary.csv"))

  stage_plot <- ggplot(stage_long, aes(x = stage, y = median_ms, fill = scenario)) +
    geom_col(position = position_dodge(width = 0.8), width = 0.7) +
    coord_flip() +
    labs(
      title = "Odessy Benchmark Stage Breakdown",
      x = NULL,
      y = "Median stage time (ms)",
      fill = NULL
    ) +
    theme_minimal(base_size = 12)

  ggsave(
    filename = file.path(output_dir, "stage_breakdown.png"),
    plot = stage_plot,
    width = 11,
    height = 7,
    dpi = 160
  )
}

cat("Wrote benchmark visualizations to: ", normalizePath(output_dir), "\n", sep = "")
