"""Initial MySQL schema baseline for fresh installs.

Existing installations must be stamped at this revision before upgrading.
"""

from alembic import op

revision = "202611080001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE `audit_logs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int DEFAULT NULL,
  `action` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `entity` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `entity_id` int DEFAULT NULL,
  `details` json DEFAULT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `customers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `customer_code` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `first_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `phone` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `shop_name` varchar(160) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `credit_limit` decimal(14,2) NOT NULL,
  `address1` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `address2` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `city` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `registered_on` date DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `customer_code` (`customer_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `held_sales` (
  `id` int NOT NULL AUTO_INCREMENT,
  `hold_ref` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `payload` json NOT NULL,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `hold_ref` (`hold_ref`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `master_items` (
  `id` int NOT NULL AUTO_INCREMENT,
  `group_name` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `unit` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_master_group_name` (`group_name`,`name`)
) ENGINE=InnoDB AUTO_INCREMENT=17 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `payment_allocations` (
  `id` int NOT NULL AUTO_INCREMENT,
  `payment_id` int NOT NULL,
  `sale_id` int NOT NULL,
  `amount` decimal(14,2) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `payment_id` (`payment_id`),
  KEY `sale_id` (`sale_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `payments` (
  `id` int NOT NULL AUTO_INCREMENT,
  `receipt_no` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `customer_id` int NOT NULL,
  `amount` decimal(14,2) NOT NULL,
  `method` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `payment_date` date NOT NULL,
  `bank_reference` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `allocation_method` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `note` text COLLATE utf8mb4_unicode_ci,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `receipt_no` (`receipt_no`),
  KEY `customer_id` (`customer_id`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `price_settings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `item_name` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `price_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `default_price` decimal(14,2) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_price_item_type` (`item_name`,`price_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `production_records` (
  `id` int NOT NULL AUTO_INCREMENT,
  `record_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `record_date` date NOT NULL,
  `variety` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `input_kg` decimal(16,3) NOT NULL,
  `primary_output_kg` decimal(16,3) NOT NULL,
  `secondary_output_kg` decimal(16,3) NOT NULL,
  `waste_kg` decimal(16,3) NOT NULL,
  `package_size_kg` int DEFAULT NULL,
  `packet_count` int DEFAULT NULL,
  `batch_no` varchar(80) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `operator` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `purchases` (
  `id` int NOT NULL AUTO_INCREMENT,
  `purchase_no` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `purchase_type` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `variety` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `seller_name` varchar(160) COLLATE utf8mb4_unicode_ci NOT NULL,
  `quantity_kg` decimal(14,3) NOT NULL,
  `rate_per_kg` decimal(14,2) NOT NULL,
  `total_amount` decimal(14,2) NOT NULL,
  `purchase_date` date NOT NULL,
  `payment_method` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `purchase_no` (`purchase_no`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `return_items` (
  `id` int NOT NULL AUTO_INCREMENT,
  `return_id` int NOT NULL,
  `sale_item_id` int NOT NULL,
  `quantity` decimal(14,3) NOT NULL,
  `total_kg` decimal(14,3) NOT NULL,
  `decision` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `inspection_notes` text COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`),
  KEY `return_id` (`return_id`),
  KEY `sale_item_id` (`sale_item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `sale_items` (
  `id` int NOT NULL AUTO_INCREMENT,
  `sale_id` int NOT NULL,
  `category` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `product_name` varchar(140) COLLATE utf8mb4_unicode_ci NOT NULL,
  `variety` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `quantity` decimal(14,3) NOT NULL,
  `unit` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `package_size_kg` int DEFAULT NULL,
  `total_kg` decimal(14,3) NOT NULL,
  `price_per_kg` decimal(14,2) NOT NULL,
  `line_total` decimal(14,2) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `sale_id` (`sale_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `sales` (
  `id` int NOT NULL AUTO_INCREMENT,
  `invoice_no` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `customer_id` int DEFAULT NULL,
  `customer_name` varchar(160) COLLATE utf8mb4_unicode_ci NOT NULL,
  `customer_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `subtotal` decimal(14,2) NOT NULL,
  `total_amount` decimal(14,2) NOT NULL,
  `amount_paid` decimal(14,2) NOT NULL,
  `amount_due` decimal(14,2) NOT NULL,
  `payment_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `payment_method` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `sale_date` datetime NOT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `invoice_no` (`invoice_no`),
  KEY `customer_id` (`customer_id`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `sales_returns` (
  `id` int NOT NULL AUTO_INCREMENT,
  `return_no` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `sale_id` int NOT NULL,
  `customer_id` int DEFAULT NULL,
  `return_date` datetime NOT NULL,
  `settlement_type` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `settlement_amount` decimal(14,2) NOT NULL,
  `status` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `return_no` (`return_no`),
  KEY `sale_id` (`sale_id`),
  KEY `customer_id` (`customer_id`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `settings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `key` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `value` text COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `key` (`key`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `stock_movements` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_id` int NOT NULL,
  `movement_type` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `quantity_kg` decimal(16,3) NOT NULL,
  `quantity_bags` decimal(16,3) NOT NULL,
  `reference_type` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `reference_id` int DEFAULT NULL,
  `notes` text COLLATE utf8mb4_unicode_ci,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `stock_id` (`stock_id`),
  KEY `created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `stocks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `item_name` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `stock_type` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `variety` varchar(80) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `quantity_kg` decimal(16,3) NOT NULL,
  `quantity_bags` decimal(16,3) NOT NULL,
  `package_size_kg` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stock_item_type_variety_pack` (`item_name`,`stock_type`,`variety`,`package_size_kg`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `suppliers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `supplier_code` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(160) COLLATE utf8mb4_unicode_ci NOT NULL,
  `phone` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `address` text COLLATE utf8mb4_unicode_ci,
  `is_active` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `supplier_code` (`supplier_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    op.execute(
        """CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `first_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(160) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `contact_number` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `permissions_json` json NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`),
  UNIQUE KEY `email` (`email`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )


def downgrade():
    raise RuntimeError("The initial schema baseline is intentionally non-destructive and cannot be downgraded.")
