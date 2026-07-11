import { Card } from "./ui";

export function ComingSoon({ title, note }: { title: string; note: string }) {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">{title}</h1>
      <Card className="p-10 text-center">
        <div className="text-3xl">🚧</div>
        <p className="mt-3 text-sm text-muted">{note}</p>
        <p className="mt-1 text-xs text-muted">Being rebuilt on the new stack.</p>
      </Card>
    </div>
  );
}
