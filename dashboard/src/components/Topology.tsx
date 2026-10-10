import { useMemo } from 'react';

interface GraphNode {
  id: string;
  critical: boolean;
  replicas: number;
  blast_radius: number;
  single_point: boolean;
}

interface GraphEdge {
  from: string;
  to: string;
}

interface Props {
  graphData: { nodes: GraphNode[]; edges: GraphEdge[] } | null;
}

const W = 800;
const H = 380;
const CX = W / 2;
const CY = H / 2;
const INNER_R = 85;
const OUTER_R = 155;

function layoutNodes(nodes: GraphNode[]) {
  if (!nodes.length) return {};

  const positions: Record<string, { x: number; y: number }> = {};

  // Put frontend in the centre
  const centre = nodes.find(n => n.id === 'frontend');
  const rest   = nodes.filter(n => n.id !== 'frontend');

  if (centre) positions['frontend'] = { x: CX, y: CY };

  // Inner ring: high blast-radius critical services
  const inner = rest.filter(n => n.blast_radius >= 3 && n.critical);
  const outer = rest.filter(n => !inner.includes(n));

  inner.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / inner.length - Math.PI / 2;
    positions[n.id] = {
      x: CX + INNER_R * Math.cos(angle),
      y: CY + INNER_R * Math.sin(angle),
    };
  });

  outer.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / outer.length - Math.PI / 2;
    positions[n.id] = {
      x: CX + OUTER_R * Math.cos(angle),
      y: CY + OUTER_R * Math.sin(angle),
    };
  });

  return positions;
}

export function Topology({ graphData }: Props) {
  const positions = useMemo(() => layoutNodes(graphData?.nodes ?? []), [graphData]);

  if (!graphData) {
    return (
      <div className="h-96 flex items-center justify-center text-gray-500">
        Loading topology…
      </div>
    );
  }

  const nodeIds = new Set(graphData.nodes.map(n => n.id));
  const safeEdges = graphData.edges.filter(e => nodeIds.has(e.from) && nodeIds.has(e.to));

  return (
    <div className="h-96 w-full bg-gray-900 rounded-xl overflow-hidden border border-gray-700 relative">
      <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`}>
        <defs>
          <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L0,6 L6,3 z" fill="#4b5563" />
          </marker>
          <filter id="glow">
            <feGaussianBlur stdDeviation="2" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Edges */}
        {safeEdges.map((e, i) => {
          const src = positions[e.from];
          const tgt = positions[e.to];
          if (!src || !tgt) return null;
          return (
            <line
              key={`edge-${i}`}
              x1={src.x} y1={src.y}
              x2={tgt.x} y2={tgt.y}
              stroke="#374151"
              strokeWidth={1.5}
              strokeOpacity={0.7}
              markerEnd="url(#arrow)"
            />
          );
        })}

        {/* Nodes */}
        {graphData.nodes.map(node => {
          const pos = positions[node.id];
          if (!pos) return null;

          const fill    = node.critical ? '#dc2626' : '#2563eb';
          const glow    = node.critical ? '#ef4444' : '#3b82f6';
          const textCol = '#f9fafb';
          const rx = 36;
          const ry = 14;

          return (
            <g key={node.id} transform={`translate(${pos.x},${pos.y})`}>
              {/* Glow ring */}
              <ellipse cx={0} cy={0} rx={rx + 4} ry={ry + 4}
                fill="none" stroke={glow} strokeWidth={1.5} strokeOpacity={0.3} />
              {/* Node body */}
              <ellipse cx={0} cy={0} rx={rx} ry={ry}
                fill={fill} fillOpacity={0.85} filter="url(#glow)" />
              {/* Service name */}
              <text x={0} y={0} textAnchor="middle" dominantBaseline="middle"
                fontSize={9} fontWeight={600} fill={textCol} style={{ userSelect: 'none' }}>
                {node.id.length > 14 ? node.id.slice(0, 13) + '…' : node.id}
              </text>
              {/* Replica badge */}
              <text x={0} y={11} textAnchor="middle" dominantBaseline="middle"
                fontSize={7} fill={textCol} fillOpacity={0.65} style={{ userSelect: 'none' }}>
                ×{node.replicas} {node.single_point ? '⚠' : ''}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="absolute bottom-2 right-3 flex gap-3 text-xs text-gray-400">
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-red-500 inline-block" /> Critical
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" /> Non-critical
        </span>
        <span className="flex items-center gap-1">⚠ Single-point</span>
      </div>
    </div>
  );
}
