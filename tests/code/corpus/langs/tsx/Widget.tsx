import React from "react";

export interface Props {
  label: string;
}

export type Theme = "light" | "dark";

export class Widget extends React.Component<Props> {
  render() {
    return <div className={badgeClass()}>{this.props.label}</div>;
  }
}

function badgeClass(): string {
  return "badge";
}

export function Badge(props: Props) {
  return <Widget label={props.label} />;
}
