"use client";

import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CitationChip } from "@/components/evidence";

const CITATION = /^\[(S[0-9a-f]{6})\]$/;

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/^\d+\.\s*/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

/**
 * Renders the backend's Markdown report. Raw HTML is never rendered (react-markdown's
 * default), and citation links of the form [[S3f9a2c]](url) become citation chips.
 */
export function MarkdownReport({ markdown, titles }: { markdown: string; titles: Record<string, string> }) {
  const components: Components = {
    h2: ({ children }) => <h2 id={slugify(textOf(children))}>{children}</h2>,
    a: ({ href, children }) => {
      const label = textOf(Children.toArray(children));
      const match = label.match(CITATION);
      if (match && href) {
        return (
          <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="!no-underline">
            <CitationChip citationKey={match[1]} title={titles[match[1]] ?? href} />
          </a>
        );
      }
      const external = href?.startsWith("http");
      return (
        <a href={href} {...(external ? { target: "_blank", rel: "noopener noreferrer nofollow" } : {})}>
          {children}
        </a>
      );
    },
  };
  return (
    <div className="report-prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
