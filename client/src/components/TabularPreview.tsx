import React from 'react';
import { TabularData } from '../types/execution';

interface TabularPreviewProps {
  data: TabularData | TabularData[];
}

export const TabularPreview: React.FC<TabularPreviewProps> = ({ data }) => {
  const tables = Array.isArray(data) ? data : [data];

  if (tables.length === 0 || (!tables[0].columns?.length && !tables[0].rows?.length)) {
    return <div className="empty-table-notice">No tabular result rows returned.</div>;
  }

  return (
    <div className="tabular-preview-container">
      {tables.map((table, tIdx) => (
        <div key={tIdx} className="table-block">
          {tables.length > 1 && <h5 className="table-heading">Result Set {tIdx + 1}</h5>}
          <div className="table-scroll-wrapper">
            <table className="pes2-data-table">
              <thead>
                <tr>
                  <th className="row-num-col">#</th>
                  {table.columns.map((col, cIdx) => (
                    <th key={cIdx}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, rIdx) => (
                  <tr key={rIdx}>
                    <td className="row-num-col">{rIdx + 1}</td>
                    {row.map((cell, cIdx) => (
                      <td key={cIdx}>
                        {cell === null ? (
                          <span className="null-badge">null</span>
                        ) : typeof cell === 'boolean' ? (
                          <span className={`bool-badge ${cell ? 'bool-true' : 'bool-false'}`}>
                            {cell ? 'true' : 'false'}
                          </span>
                        ) : (
                          String(cell)
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-meta">
            {table.rows.length} row{table.rows.length !== 1 ? 's' : ''} in result set
          </div>
        </div>
      ))}
    </div>
  );
};
