"use client";

import { Card, Title, Table, TableHead, TableHeaderCell, TableBody, TableRow, TableCell, Badge } from "@tremor/react";
import type { SpreadInfo } from "@/lib/types";

interface Props {
  spreads: Record<string, SpreadInfo>;
}

export default function LiveTickers({ spreads }: Props) {
  const entries = Object.entries(spreads);

  if (entries.length === 0) {
    return (
      <Card>
        <Title>Live Spreads</Title>
        <p className="text-gray-400 mt-4 text-sm">Waiting for data...</p>
      </Card>
    );
  }

  return (
    <Card>
      <Title>Live Spreads</Title>
      <Table className="mt-4">
        <TableHead>
          <TableRow>
            <TableHeaderCell>Symbol</TableHeaderCell>
            <TableHeaderCell>Buy On</TableHeaderCell>
            <TableHeaderCell>Buy Price</TableHeaderCell>
            <TableHeaderCell>Sell On</TableHeaderCell>
            <TableHeaderCell>Sell Price</TableHeaderCell>
            <TableHeaderCell>Spread</TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {entries.map(([symbol, s]) => (
            <TableRow key={symbol}>
              <TableCell className="font-medium">{symbol}</TableCell>
              <TableCell>
                <Badge color="emerald" size="sm">{s.buy_exchange}</Badge>
              </TableCell>
              <TableCell>${s.buy_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</TableCell>
              <TableCell>
                <Badge color="rose" size="sm">{s.sell_exchange}</Badge>
              </TableCell>
              <TableCell>${s.sell_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</TableCell>
              <TableCell>
                <Badge color={s.spread_pct > 0 ? "emerald" : "gray"} size="sm">
                  {s.spread_pct.toFixed(4)}%
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
