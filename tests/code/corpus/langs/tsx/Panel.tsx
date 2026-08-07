import { Badge } from "./Widget";

export interface PanelProps {
  title: string;
}

export function Panel(props: PanelProps) {
  return <section>{renderTitle(props.title)}</section>;
}

function renderTitle(title: string) {
  return <Badge label={title} />;
}
