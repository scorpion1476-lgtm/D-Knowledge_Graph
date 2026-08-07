library(ggplot2)

make_plot <- function(data) {
  ggplot(data)
}

save_plot <- function(plot, path) {
  make_plot(plot)
}
