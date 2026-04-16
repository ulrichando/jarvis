// Stub: ConnectorText types not present in the public source.

export interface ConnectorTextBlock {
  type: 'connector_text'
  connector_text: string
}

export function isConnectorTextBlock(block: unknown): block is ConnectorTextBlock {
  return (
    typeof block === 'object' &&
    block !== null &&
    (block as ConnectorTextBlock).type === 'connector_text'
  )
}
