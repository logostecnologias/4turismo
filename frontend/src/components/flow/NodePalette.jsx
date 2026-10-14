import { NODE_TYPES, NODE_ORDER } from "./nodeTypes";

export default function NodePalette({ onDragStart }) {
  const onDragStartInternal = (event, nodeType) => {
    event.dataTransfer.setData("application/reactflow", nodeType);
    event.dataTransfer.effectAllowed = "move";
    onDragStart?.(nodeType);
  };

  return (
    <div className="p-3 space-y-1 overflow-y-auto flex-1" data-testid="node-palette">
      <div className="text-xs font-medium text-ink-3 mb-2 px-1">
        Blocos disponíveis
      </div>
      {NODE_ORDER.filter((t) => t !== "start").map((type) => {
        const meta = NODE_TYPES[type];
        const Icon = meta.icon;
        return (
          <div
            key={type}
            draggable
            onDragStart={(e) => onDragStartInternal(e, type)}
            data-testid={`palette-${type}`}
            className="flex items-center gap-2 p-3 border border-line bg-surface cursor-grab hover:border-line-strong active:cursor-grabbing transition-colors rounded-lg"
          >
            <div
              className="w-7 h-7 flex items-center justify-center border border-line-strong rounded-md"
              style={{ background: meta.color, color: meta.textColor || "#0F172A" }}
            >
              <Icon size={14} weight="regular" />
            </div>
            <div className="text-xs font-semibold text-ink">{meta.label}</div>
          </div>
        );
      })}
    </div>
  );
}
