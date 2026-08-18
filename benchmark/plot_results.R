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

numeric_cols <- c(
  "iteration",
  "total_ms",
  "benchmark_process_rss_before_bytes",
  "benchmark_process_rss_after_bytes",
  "benchmark_process_swap_before_bytes",
  "benchmark_process_swap_after_bytes",
  "process_tree_rss_before_bytes",
  "process_tree_rss_after_bytes",
  "process_tree_swap_before_bytes",
  "process_tree_swap_after_bytes",
  "system_ram_used_before_bytes",
  "system_ram_used_after_bytes",
  "system_ram_available_before_bytes",
  "system_ram_available_after_bytes",
  "system_swap_used_before_bytes",
  "system_swap_used_after_bytes",
  "system_swap_available_before_bytes",
  "system_swap_available_after_bytes",
  "record_ms",
  "transcribe_ms",
  "proposal_ms",
  "response_ms",
  "agent_ms",
  "synthesize_ms",
  "play_ms"
)

samples <- read_csv(
  samples_path,
  show_col_types = FALSE,
  col_types = cols(.default = col_guess())
) %>%
  mutate(across(any_of(numeric_cols), as.numeric))

metadata <- if (file.exists(metadata_path)) {
  fromJSON(metadata_path, simplifyVector = TRUE)
} else {
  NULL
}

measured <- samples %>% filter(!is_warmup)

stage_cols <- c("record_ms", "transcribe_ms", "proposal_ms", "response_ms", "agent_ms", "synthesize_ms", "play_ms")
stage_cols <- stage_cols[stage_cols %in% names(measured)]

memory_to_mb <- function(x) {
  x / 1024 / 1024
}

plot_memory_scope <- function(data, metrics, labels, filename, title) {
  memory_long <- data %>%
    select(scenario, iteration, all_of(metrics)) %>%
    pivot_longer(cols = all_of(metrics), names_to = "metric", values_to = "bytes") %>%
    mutate(metric = recode(metric, !!!labels), value_mb = memory_to_mb(bytes)) %>%
    filter(!is.na(value_mb))

  plot <- ggplot(memory_long, aes(x = iteration, y = value_mb, color = metric, group = metric)) +
    geom_line(linewidth = 0.7) +
    geom_point(size = 1.8) +
    facet_wrap(~scenario, scales = "free_y") +
    labs(title = paste("Odessy", title), x = "Iteration", y = "Memory (MB)", color = NULL) +
    theme_minimal(base_size = 12)

  ggsave(filename = filename, plot = plot, width = 12, height = 7, dpi = 160)
}

safe_summary <- function(values, fn) {
  values <- values[!is.na(values)]
  if (length(values) == 0) {
    return(NA_real_)
  }
  as.numeric(fn(values))
}

summary_tbl <- measured %>%
  group_by(scenario) %>%
  summarise(
    runs = sum(status == "ok", na.rm = TRUE),
    failed = sum(status != "ok", na.rm = TRUE),
    mean_ms = safe_summary(total_ms[status == "ok"], mean),
    median_ms = safe_summary(total_ms[status == "ok"], median),
    p95_ms = safe_summary(total_ms[status == "ok"], function(x) quantile(x, 0.95, na.rm = TRUE, type = 7)),
    max_ms = safe_summary(total_ms[status == "ok"], max),
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

ggsave(file.path(output_dir, "latency_by_scenario.png"), latency_plot, width = 10, height = 6, dpi = 160)

plot_memory_scope(
  measured,
  c(
    "benchmark_process_rss_before_bytes",
    "benchmark_process_rss_after_bytes",
    "benchmark_process_swap_before_bytes",
    "benchmark_process_swap_after_bytes"
  ),
  c(
    benchmark_process_rss_before_bytes = "RSS before",
    benchmark_process_rss_after_bytes = "RSS after",
    benchmark_process_swap_before_bytes = "Swap before",
    benchmark_process_swap_after_bytes = "Swap after"
  ),
  file.path(output_dir, "memory_by_scenario.png"),
  "Benchmark Process Memory"
)

plot_memory_scope(
  measured,
  c(
    "process_tree_rss_before_bytes",
    "process_tree_rss_after_bytes",
    "process_tree_swap_before_bytes",
    "process_tree_swap_after_bytes"
  ),
  c(
    process_tree_rss_before_bytes = "RSS before",
    process_tree_rss_after_bytes = "RSS after",
    process_tree_swap_before_bytes = "Swap before",
    process_tree_swap_after_bytes = "Swap after"
  ),
  file.path(output_dir, "process_tree_memory_by_scenario.png"),
  "Process Tree Memory"
)

plot_memory_scope(
  measured,
  c(
    "system_ram_used_before_bytes",
    "system_ram_used_after_bytes",
    "system_ram_available_before_bytes",
    "system_ram_available_after_bytes",
    "system_swap_used_before_bytes",
    "system_swap_used_after_bytes",
    "system_swap_available_before_bytes",
    "system_swap_available_after_bytes"
  ),
  c(
    system_ram_used_before_bytes = "RAM used before",
    system_ram_used_after_bytes = "RAM used after",
    system_ram_available_before_bytes = "RAM available before",
    system_ram_available_after_bytes = "RAM available after",
    system_swap_used_before_bytes = "Swap used before",
    system_swap_used_after_bytes = "Swap used after",
    system_swap_available_before_bytes = "Swap available before",
    system_swap_available_after_bytes = "Swap available after"
  ),
  file.path(output_dir, "system_memory_by_scenario.png"),
  "System Memory"
)

if (length(stage_cols) > 0) {
  stage_long <- measured %>%
    select(scenario, iteration, all_of(stage_cols)) %>%
    pivot_longer(cols = all_of(stage_cols), names_to = "stage", values_to = "stage_ms") %>%
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

  ggsave(file.path(output_dir, "stage_breakdown.png"), stage_plot, width = 11, height = 7, dpi = 160)
}

cat("Wrote benchmark visualizations to: ", normalizePath(output_dir), "\n", sep = "")
