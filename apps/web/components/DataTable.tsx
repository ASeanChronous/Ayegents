export type Column<T> = {
  header: string;
  render: (row: T) => React.ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  keyFn,
  emptyMessage = "Nothing here yet.",
}: {
  columns: Column<T>[];
  rows: T[];
  keyFn: (row: T) => string;
  emptyMessage?: string;
}) {
  if (rows.length === 0) {
    return <p className="rounded-md border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">{emptyMessage}</p>;
  }

  return (
    <table className="w-full border-collapse overflow-hidden rounded-md border border-gray-200 text-sm">
      <thead>
        <tr className="bg-gray-50 text-left text-gray-600">
          {columns.map((col) => (
            <th key={col.header} className="border-b border-gray-200 px-4 py-2 font-medium">
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={keyFn(row)} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
            {columns.map((col) => (
              <td key={col.header} className="px-4 py-2">
                {col.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
