import type { ButtonHTMLAttributes } from "react";

type IconName = "close" | "edit";

function Icon({ name }: { name: IconName }) {
  if (name === "edit") {
    return (
      <svg aria-hidden="true" viewBox="0 0 20 20" fill="none">
        <path d="M4 13.8V16h2.2L15 7.2 12.8 5 4 13.8Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
        <path d="m11.9 5.9 2.2 2.2" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="none">
      <path d="m6 6 8 8m0-8-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function IconButton({
  icon,
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName }) {
  return (
    <button type={type} className={`icon-btn ui-icon-button ${className}`.trim()} {...props}>
      <Icon name={icon} />
    </button>
  );
}

